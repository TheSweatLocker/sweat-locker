# BACKLOG — living

**Last verified: 2026-10-07 (late night) — run `python mlb_pipeline/verify_backlog.py` before quoting any number in this file**

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

### B10 · NFL props have no projection — **CLOSED 2026-09-24**
`2606fedf` (writer) + `backfill_nfl_prop_projection.py` (history).
**projection now populated on 1,805 of 1,807.** The generator always computed
it; `_to_pipeline_props_shape` never mapped it, and `nfl_props` — the table the
docstring claimed was a dual-write target — has been dead since 09-03, so the
value was discarded on every write with a 200 response. Three signal_sources
rows gating on `p.get('projection')` were dead from 08-22.

**But the projection does not pay.** Measured on 1,331 graded props: 50.9%
overall, best bucket 51.8%, against a **54.2%** break-even at the prices we
actually paid. Buckets are non-monotonic and the probability is badly
overconfident (says 93%, gets 59%). `backfill_prop_signal_tiers` was run and
`edge_weight` correctly zeroed both projection signals on that evidence. So the
data gap is closed and the modelling question is open — see B36 and
`project_nfl_prop_scoring_diagnosis_924`.

VERIFY: `python mlb_pipeline/verify_backlog.py --only B10`
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

### B26 · Markdown leaking into short_read — **CLOSED 2026-09-24**
`20309487`. Established first, not assumed: reads render in a plain React
Native `<Text>` (app/index.tsx:14870, :16029) via `scrubJerryText`, which only
strips `[Auto-...]` tags. **package.json has no markdown dependency** and
nothing converts `**`, so subscribers saw literal asterisks.

Scope was larger than this item said — it logged short_read only:
**19 of 152 forward-slate reads** carried `**`, 8 in short_read and 19 in
long_read, across all four sports. One NFL short_read was just `**` and a
newline. No `#` headers, no bullets.

`strip_markdown_emphasis` (jerry_reads_dual_write) strips bold/italic and any
unpaired `**`, and deliberately leaves em dashes, `---` rules and arithmetic
asterisks alone — 10 test inputs, 0 failures. Wired into the shared writer
(covers NCAAF/NCAAB/NHL), `generate_nfl_game_reads`, and
`sync_jerry_reads_from_ctx`. Also added to `jerry_pre_publish_audit.auto_repair`
so any of the ~30 writers not patched gets cleaned nightly — writer-side
prevention failed three times today on the parser-marker case, so the boundary
repair is the part that cannot be outflanked.

VERIFIED: 152 reads, 0 `**` in either field, 0 parser markers, 0 blanked
fields, min short/long 122/406 chars.
VERIFY: `python mlb_pipeline/verify_backlog.py --only B26`

Original finding follows.

### B26-orig · Markdown leaking into short_read
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

### B43 - we publish picks the market does not offer
Found 2026-09-23 while pricing the slate. Seven NCAAF picks name a line
no book quotes, and two of them are on the OPPOSITE SIDE of the market:

      Oregon @ USC        published "USC -2.5"        STRONG conv 83
                          every book has USC at +3.0
      OkSt @ West Virginia published "West Virginia +2.5"  conv 71
                          every book has WVU at -1.0 to -2.0

USC is a three-point underdog. We told a subscriber to lay two and a half
points on a dog. That is not a stale number - it is the opposite team,
and no model confidence makes it placeable.

The reads are FAITHFUL. Every published line matches
ncaaf_game_context.close_spread exactly. The defect is in the context
row, not the prose or the LLM.

WHAT IS NOT THE TEST. I first framed this as a close_spread sign bug and
counted sign inversions across all sports - 2 NFL, 2 NCAAF, 10 MLB. Most
are legitimate: PHI @ CHI opened home -1.5 and closed home +4.5, a real
six-point move, and CHI +4.5 is quoted at ten books. MLB's +/-1.5 flips
are run lines where the favourite changed. Sign movement is normal.

THE TEST is whether any book quotes the exact thing we named. Today:

      WRONG_SIDE  2     DRIFTED  5     NO_QUOTES  29     OK  33

All seven bad ones are NCAAF. MLB, NFL and NHL are clean.

ROOT CAUSE FOUND 2026-09-23. Not a sign convention bug at all.

ncaaf_odds_pull.py was correct the whole time - it reads Oregon @ USC as
sp=3.0, ml=140/-166, which matches the books exactly, and writes that to
ncaaf_game_results. The defect was one layer later:

  * ncaaf_game_results holds the same fixture twice for neutral-site and
    UTC-boundary games - ncaaf_20261010_Oklahoma_Texas alongside
    ncaaf_20261010_Texas_Oklahoma, and Georgia_Alabama on both 10-10 and
    10-11 with OPPOSITE spread signs.
  * ncaaf_game_context has a unique index on (game_date, LEAST(home,away),
    GREATEST(home,away)) which correctly rejects them.
  * the context upsert POSTs all rows in ONE request, which is atomic, so
    that single conflicting pair aborted all 90 writes and returned 0.
  * the workflow wraps the call in `|| echo "game_context failed"`.

So every NCAAF context row was frozen at 2026-09-05 while team form,
projections and reads kept refreshing around it. Three weeks of stale
lines, announced by one echo line nobody reads. B11's silent-failure
class, at the writer.

FIXED: the upsert now falls back to one request per row on 409. Good rows
land, conflicting fixtures are named. 88 of 90 wrote; the 2 rejected are
exactly the duplicate pair. Oregon @ USC corrected -2.5 -> +3.0 and its
primary_play flipped from "USC -2.5" to "Oregon ML".

STILL OPEN: dedupe ncaaf_game_results itself. Two rows for one fixture is
the upstream defect; the resilient upsert only stops it being fatal.

FIX:
  1. `verify_offerable.py` exists and exits 2 on WRONG_SIDE. Wire it as a
     publish gate - a pick the market does not offer must not ship.
  2. Root-cause why ncaaf_game_context.close_spread inverts. Oregon @ USC
     stored open_spread=2.5 and close_spread=-2.5 with the magnitude
     unchanged, which no real market move produces.
  3. DRIFTED picks should refresh their line, not ship stale. Miami @
     Clemson published +7 against a market of +17.5.

VERIFY: `python mlb_pipeline/verify_offerable.py`

### B44 - NFL externals: 7 sources against MLB's 13 - **PARTLY CLOSED 2026-10-07** `d0977c18`, see the 10-07 section
Andy 2026-09-24: "NFL needs more sources."

Last 3 days, verified:

      MLB     414 picks   12 sources
      NFL     147 picks    4 sources
      NCAAF    41 picks    2 sources
      UFC      17 picks    1 source

NFL is the second-biggest surface in the app and carries a third of MLB's
source coverage. pickdawgz is wired for NFL but thin (9 picks/week against
~16 published articles) because the puller reads the landing page, which
only exposes about ten articles at a time. Crawl the per-sport archive
instead and run closer to slate time.

Sources present for MLB and absent for NFL: action, sbr, docsports,
betfirm, bettingpros, dimers, vsin, tonyspicks, oddscrowd (partial).
Several are known to cover NFL.

VERIFY: count external_picks by (sport, source) over a 7-day window.

### B45 - a fixed grader lookback turns a transient failure into a permanent gap
Root-caused 2026-09-24 while grading 46 stranded props.

      nfl_pipeline.yml: resolve_nfl_props_espn.py --lookback 3
                        || echo "ESPN prop grader failed (non-fatal)"

Two faults compounding:

  1. THE ALIAS. ESPN calls Washington "WSH"; we store "WAS". The exact
     pair lookup missed and the substring fallback could not save it
     ('WSH' in 'WAS' is False both ways), so every prop on WAS @ DAL was
     marked ungradeable - on a STATUS_FINAL game we already held a 20-37
     score for. 19 of the 46 were STRONG tier: publishable plays absent
     from the record. It would have recurred every week Washington played.
     FIXED: the grader now indexes every spelling from nfl_team_aliases,
     which already mapped WSH -> WAS and 2,800 other variants and simply
     was not being consulted. 44 of 46 graded immediately; NFL ungraded
     backlog 46 -> 0.
  2. THE WINDOW. --lookback 3 means anything that fails for three days is
     unreachable forever. Combined with `|| echo` nobody learns either.
     STILL OPEN: the grader needs a catch-up pass with no fixed horizon -
     "grade anything unresolved whose game is final", not "look back N
     days". Same shape as the MLB yesterday-catch-up step.

VERIFY: `python mlb_pipeline/resolve_nfl_props_espn.py --lookback 10 --dry-run`
        should report 0 unresolved on every date.

### B46 - NHL publishes 37 picks with no reasoning — **CLOSED 2026-09-24**
`1d74e499` wired `generate_nhl_game_reads.py`. Verified: **37 NHL reads, 0
engine-sub shorts, 0 with long_read under 300 chars.** The horizon was the
second half of the fix — the generator first copied NCAAB's "today + 5 days"
and left three games stubbed because `nhl_game_context` reached further than
the generator did; it now reads whatever context holds.

Follow-ups that are NOT closed: 29 of the 37 carry no price (preseason, no book
quotes), and every one of them makes a fabricated product claim — see B48.

VERIFY: `python mlb_pipeline/verify_backlog.py --only B46`

Original finding follows.

Found 2026-09-24 while clearing duplicate reads.

Every NHL read on the board is the engine's own `sub` string, 31-33
characters, with long_read EMPTY:

      "Model conviction on Canucks · 64%"    conv 64
      "Model conviction on Kraken · 63%"     conv 63
      "Model conviction on Sharks · 60%"     conv 60

37 of 37, covering 09-24 through 09-30, each attached to a real ML pick.
Worse than the NFL stub incident and nobody had looked.

ROOT CAUSE: nhl_pipeline.yml has NO read generator. It runs nhl_odds_pull,
nhl_resolve_results, nhl_game_context, pull_externals_nhl,
backfill_nhl_team_tendencies and nhl_generate_props — nothing writes
prose. sync_jerry_reads_from_ctx.py then backfills primary_play.sub as the
visible short_read, exactly as it did for NFL when that generator failed.

NOT a new build. The pattern exists four times over
(generate_jerry_synthesis, generate_ncaab_game_reads,
generate_ncaaf_game_reads, generate_nfl_game_reads all share
jerry_reads_dual_write), and _prompt_game_read_rules_NHL.txt is already
written. It is 10 lines and it already states the honest framing:

      "Market-based analysis — no NHL model active yet."
      "Do NOT fabricate model metrics."
      Lead on confirmed goalie starters, pace, special teams, form.

So the intent was decided; the wiring was never done. generate_ncaab_game_reads
is the closest analog — another soft-launched sport.

DECISION NEEDED: NHL opens 10-08. Either wire the generator before then,
or stop publishing NHL picks until it exists. Shipping a conviction-64 ML
with 33 characters of engine output as its analysis is the worse of the
two.

ALSO: 29 of 37 NHL reads carry no price — line_history has no quotes for
preseason games.

VERIFY: `select count(*) from jerry_reads where sport='NHL'
         and length(coalesce(long_read,''))=0 and game_date>=current_date;`

### B48 - every NHL read promises a product we have not built
Found 2026-09-24 while auditing this file against reality.

**37 of 37 live NHL reads** tell subscribers a proprietary NHL model
**launches in the 2026-27 season**. Nobody authorised that claim. 17 of them
also render it with a typo — "2026-27season", no space.

      "Market-based analysis — proprietary NHL model launches 2026-27season."

IT IS AUTHORED, NOT HALLUCINATED. Corrected within the hour of first writing
this entry. The live prompt the generator actually loads —
`prompt_templates` sport=NHL name=game_read_rules, is_active, version 2 —
says it verbatim:

      - Open with one line: "Market-based analysis — proprietary NHL model
        launches 2026-27 season."

So the LLM followed instructions exactly. Its only contribution is dropping the
space: "2026-27season" on 17 of 37.

WHAT MISLED ME, and it is its own problem.
`mlb_pipeline/_prompt_game_read_rules_NHL.txt` line 3 says 'Open with one line:
"Market-based analysis — no NHL model active yet."' — the honest version. That
file is read by NO python in the repo and is UNTRACKED in git. A dead reference
copy that contradicts production, sitting next to the code, which is exactly
how you get a confident wrong diagnosis. I wrote the first version of this entry
blaming the model for a sentence a human wrote in May.

My earlier retraction of this same flag was therefore CORRECT, and tonight's
un-retraction was the error. Only NHL's prompt carries the claim; no other
sport's does.

TWO SEPARATE THINGS TO DECIDE AND FIX:

  1. THE CLAIM IS ANDY'S CALL, not an engineering fix. Does The Sweat Locker
     want to promise subscribers a proprietary NHL model in 2026-27? It has
     been shipping on every NHL read since 2026-05-13. It is also
     self-contradicting: the same sentence explains the analysis is
     market-based because no model exists, then commits to when one arrives.

  2. THE MECHANISM IS A DEFECT REGARDLESS. Fixed product copy should not be
     generated text. Because the LLM paraphrases it, we get a typo on 17 of 37
     reads and no single place to edit the wording. Prepend it server-side from
     one constant and drop the instruction from the prompt — that fixes the
     typo permanently, makes the sentence one editable string, and removes the
     LLM from a claim about the roadmap. Doing this preserves the CURRENT
     wording verbatim so it carries no decision about (1).

Also: the dead `.txt` should be deleted or reconciled with the DB row. Right now
it is a trap.

Same class as B26 (markdown in short_read): nothing enforces the boundary
between generated prose and fixed product copy.

VERIFY: `python mlb_pipeline/verify_backlog.py --only NEW`

---

### B50 - the MLB workflow is load-bearing for three sports that have no records of their own
Found 2026-09-24. **Andy: not now — later this week. Must land before 2026-10-08
(NHL opening night).**

`mlb_pipeline.yml` is 173 steps in ONE fully serial job. 159 are MLB-scoped, but
**7 are load-bearing for other sports**:

      step   6   yesterday catch-up grading — NFL + NCAAF
      step  39   compute_scenario_audit — NCAAF / NCAAB / NHL
      step  49   generate_prop_jerry_synthesis — NO --sport, so every sport
      step  85   compute_surface_records — every sport
      step 157   aggregate_daily_records — every sport
      step 158   rescue pass — MC + props + ladder + ledger

THE BLOCKER, and it is the reason this cannot be gated first: **NHL, NBA and
NCAAB have no records steps at all.** All three workflows are missing BOTH
`compute_surface_records` and `aggregate_daily_records` — their records are
computed only by the MLB pipeline. `compute_scenario_audit` likewise exists only
in mlb_pipeline.yml. NFL and NCAAF are fine; they call both themselves.

So gating or shortening the MLB pipeline for the offseason, before those steps
move, silently stops record-keeping for the three sports about to come online —
NHL 10-08, NBA 10-21, NCAAB 11-03. Same failure shape as everything else found
today: nothing errors, records just stop.

ORDER OF WORK:
  1. Move the 7 cross-sport steps into a sport-agnostic workflow.
     `mlb_grade_overnight.yml` is the natural home — 24 steps, runs 3x/day on
     its own cron, already calls both records scripts.
  2. THEN the 166 MLB-only steps can be seasonally gated.

NOT YET VERIFIED, and step 1 should not start without it: whether those 7 steps
have ordering dependencies on MLB steps that run before them. Moving a step that
depends on something upstream is a new bug, not a fix. Check what each reads
before it writes.

Urgency split: step 1 is deadline-bound (10-08). Step 2 is not — MLB runs
through the postseason to ~11-05, so the wasted runtime costs minutes, not
correctness.

VERIFY (the 3 sports missing records steps):
      for f in nhl nba ncaab; do grep -c compute_surface_records \
        .github/workflows/${f}_pipeline.yml; done      # expect 0 0 0

---

### B49 - MLB's publish gate: posture FIXED 2026-09-24, three criticals still open
`2a4eab4c`. The gate's accuracy was fixed first (no sport filter — 188 rows where
MLB had 145; unpaginated props fetch — 1,000 of 1,127), verified not to move the
answer (58/22 either way), and only then given the ability to redden the run.
`--warn-only` dropped, call routed through run_step: the script exits 1 as
designed, run_step records it and returns 0, every later step still runs (card
137, ledger 154, daily records 157), and the job gate turns the run red.

The 2026-08-22 decision not to let this kill the pipeline STANDS — blocking was
considered and rejected, because generate_sharp_card is step 102, BEFORE the
gate, so a block cannot protect the Steam Room anyway and would only cost the
card, ledger and records.

THE THREE REAL CRITICALS REMAIN OPEN — see below.
Found 2026-09-24 while running the audit for the markdown sweep.

`jerry_pre_publish_audit` is designed to exit 1 so the sweat card build is
skipped — its own docstring calls that "the cost of a bad read shipping > cost
of a missed cron". `mlb_pipeline.yml` invokes it with **`--warn-only`**, so it
has never blocked anything. Today's MLB slate reports:

      engine_breakdown: 0/12 rows on ensemble_v2 — ensemble silently disabled
      refit coverage 38% (22/58) — apply_prop_refit likely failed this cycle
      sharp-fade discipline violation · LA@SEA UNDER, sharp 79% same side
      prop_jerry Cam Schlittler er_under LEAN on refit=7.4 (trap zone)

THE "ENSEMBLE DISABLED" ONE WAS A FALSE POSITIVE — **fixed 2026-09-24**, and my
first version of this entry repeated it as fact.

I wrote that `lr_v1` deciding every game meant "the ensemble contributes
nothing". Andy pushed back: picks were clearly being made on those days. He was
right and I had not checked what `lr_v1` means.

`lr_v1` is stamped by `defensive_gates.py` when the **LR override deliberately
supersedes** the ensemble's pick. The ensemble runs first and produces a pick;
LR then overrides it and restamps `_engine`. `watchdogs.py` has had this correct
since 2026-09-09 — `MODERN = {'ensemble_v2','lr_v1','lr_v2'}` with the note that
these "represent LR intentionally overriding, not a stale ensemble result".

`audit_engine_breakdown` was written 2026-08-17, before the LR override existed.
It counted only `ensemble_v2`, bucketed `lr_v1` into `other`, and fired critical
whenever `ensemble_v2 == 0`. Two files in the same repo disagreed about what
`lr_v1` means, and the audit lost.

Fixed: the check now treats lr_v1/lr_v2 as modern, fires critical only when NO
modern engine is present (everything on legacy or untagged), and warns — not
criticals — when LR takes an entire slate, pointing at
`mlb_lr_dissent_audit.py`. MLB now reports 3 criticals and 2 warnings instead of
4 and 1.

Cost of the false positive: a daily critical nobody could act on, which is how
the other three in the list stayed unexamined for days. Distinguishing a broken
check from a broken pipeline is the whole job of a gate.

STILL OPEN, and these three are real:
  1. sharp-fade discipline violation · LA@SEA UNDER, sharp 79% same side, 2
     models against
  2. `prop_jerry` Cam Schlittler er_under LEAN on refit=7.4 —
     apply_refit_verdict_override should have downgraded it
  3. refit coverage 38% (22/58), with the audit's own remedy: rerun
     `apply_prop_refit.py`
  4. `--warn-only` makes the gate decorative on the sport with the most reads.
     The NFL wiring added today records failures and turns the run red without
     skipping the graders (which run after the card in that workflow); MLB
     should get the same treatment rather than silence.

VERIFY: `python mlb_pipeline/jerry_pre_publish_audit.py --sport MLB --date <today>`
(exits 1 and lists them; add --warn-only to reproduce what the cron sees)

---

## P2 — structural (the ones that keep causing the others)

### B47 - NFL prop publish gate - **CLOSED 2026-10-07: R3 REJECTED out-of-sample, status quo retained**
Found 2026-09-24. **Andy concurred with the R3 candidate on 09-24 and asked
to see results after the 2026-09-27 weekend before it gates anything. Check
this after that weekend.**

`v_nfl_props_publishable` gates on `tier IN ('PRIME','STRONG')`. What that
actually publishes, measured on 1,331 graded props to 09-21 at the prices we
paid:

      R1 (live)   202-189   51.7%   needs 54.6%   ROI -5.21%   -20.4u  n=391

LEAN is never published. It is the largest tier (627 rows) and the most
profitable. PRIME stopped occurring after 09-13 (57 rows total, all on
09-09/09-10/09-13), so in practice users see STRONG and nothing else — 51 of
51 on the 09-24 slate were STRONG.

CANDIDATE R3: publish PRIME+STRONG+LEAN, counting stats only, inside the
-150..+150 price band.

      R3          198-162   55.0%   needs 52.8%   ROI +4.22%   +15.2u  n=360

Same exposure as today (360 vs 391), ROI +9.43pp, +35.5u. Neither filter
works alone — price gate only is -3.80%, counting only is +1.07%.

WHY NOT NOW: both filters were chosen by looking at the same rows they score
on. The mechanisms are sound (juice outrunning the hit rate; yardage decided
by explosive plays) and the effects are large, but in-sample selection always
flatters. The 09-27 slate is the first honest test.

STILL OPEN INSIDE R3: the ladder is mildly inverted — LEAN +5.08% (n=185)
beats STRONG +2.33% (n=146). Both profitable, so it is a labelling question
rather than a money question. Three options were put to Andy: accept it,
re-cut the conviction bands to match measured ROI order, or collapse to one
published label (the calibration map only resolved two confidence levels, so
four tiers was always more resolution than the data supports).

IF IMPLEMENTING: `v_nfl_props_publishable` already carries a tier whitelist,
a conviction floor, a kickoff filter and a volume cap. Rebuild it from the
LIVE definition, not from a migration file — see
`feedback_publishable_view_drift`, a view replacement has already silently
dropped a prior WHERE clause once. Verify the published count before and
after.

VERIFY (the Monday command):
      python mlb_pipeline/audit_nfl_publish_rules.py --since 2026-09-27 --by-tier
Compares R1 against R3 and four alternatives on graded results only, and
labels the window in-sample or out-of-sample. It reads nfl_pipeline_props
rather than public_receipts on purpose: NFL rows there are 998 reconstructed
against 2 live. Safe to read after the fact because nfl_generate_props only
builds rows for games -1h to +14 days out, so a row freezes at kickoff.

SHIPPED ALREADY (not waiting on the weekend):
  * `-150..+150` price band at source in nfl_generate_props, env-overridable
    via NFL_PROP_ODDS_MIN/MAX — `33f90b12`. 11 of 51 publishable props on the
    09-24 slate sat outside it.
  * Both sides of every market now persisted so the vig can be removed —
    `9a1fe183`. Cannot be backtested; zero of 1,331 graded props held both
    prices. MLB already stored both (99%), so this was NFL-only.

SCOPE: NFL props only. MLB's view gates on `tier != 'SKIP'` so it already
publishes LEAN, and MLB's clean post-leak window is three days (PRIME n=29,
STRONG n=23) with the season ending 09-25 — it cannot be measured this year.

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
- **B18** ~~51 bare `|| echo` workflow steps mask failures.~~ **CLOSED
  2026-09-24** `34451935` (NFL) + `d20bc460` (remaining 8 files). The real
  count was 104 across 9 workflows, not 51. NOT stripped — removing `|| echo`
  aborts every later step in the job, so one flaky scraper would take out the
  graders behind it. Continuing is correct; continuing silently and then
  reporting success is the bug. `.github/scripts/run_step.sh` records each
  failure (step summary + `::warning::` + tally file) and still returns 0; a
  per-job gate with `if: always()` reads the tally and fails the run with the
  list. Every step still executes, and a run containing a failure can no longer
  be green.
  VERIFY: `for f in .github/workflows/*.yml; do grep "|| echo" $f | grep -v "^\s*#"; done`
  should print nothing. Commented `|| echo` lines are kept as incident
  documentation.

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

---

# 2026-10-06 — ONE ROOT CAUSE, SIX SYMPTOMS

Everything below traces to **B12** (rows stay mutable) and **B32** (we never
store the closing price). Both were already logged. Neither was closed. Today
they produced a user-visible contradiction on a postseason card.

## R1 · Immutable receipts published from mutable tables — THE root cause

`mlb_pipeline_props` is deleted and re-inserted during the day. Measured
2026-10-06: 434 rows to 227 **between two queries minutes apart**; batches
written 18:39 (164), 18:40 (4), 22:42 (22), 22:43 (37), all with fresh ids.

Six symptoms, one cause:

1. Nick Pivetta Over 3.5 Ks carried FOUR convictions — Sharp 24, Sweat Card
   65, prop_jerry 50, source row 83 — each surface read a different
   generation. No component was wrong.
2. "Mike Yastrzemski Under 0.5 Hits" shipped PRIME/75 and has **no source
   row at all**.
3. Receipts cite `prop_jerry_reads` ids (135052/135054/135064) that are gone.
4. 238 prop receipts permanently unsettleable, back to June.
5. The Padres/Brewers card contradiction — the card froze a `jerry_reads` row
   that was rewritten underneath it.
6. It corrupted MY OWN analysis: a join dropped 610 of 1,216 receipts (50%)
   because it could only see surviving source rows, and the bias concentrated
   in STRONG (88% of STRONG receipts had their source deleted, leaving n=33
   that ran 45.5%) — which is what made the tier ladder look "inverted".

Pinned so an ungraded receipt's source is never deleted: `6e24d001`
(prop_jerry_reads, by id) and `1d13e37a` (props, by composite key). Both fail
closed. **THE CHURN ITSELF IS NOT FIXED** — rows still get rebuilt with new
ids and different convictions mid-day.

FIX (not done): make prop rows stable — upsert in place and never delete a
row for a game that has not started. The `on_conflict` target is already the
full natural key (game_date, player_name, prop_type, direction, prop_line),
so the DELETES are what break id stability, not the writes.

VERIFY: count rows and the min/max id for today twice, ten minutes apart, and
diff them; or check `created_at` clustering — batches minutes apart mean a
rebuild happened.

## R2 · We publish a worse price than the one we already observed (extends B32)

Of 408 reads carrying both a taken and a best-available price:

    pick price == best available     28  ( 7%)
    pick price WORSE than best      380  (93%)
    median give-up when worse       1.11pp of implied probability

For scale: PRIME's measured shortfall is **-2.1pp** (59.2% delivered vs 61.3%
implied). So roughly half of it may be self-inflicted at the point of
recording rather than a selection problem.

Also confirmed: `pick_odds` is the PUBLISH price, not the close — 318 of 326
game_read receipts match `price_american`, **zero** match
`close_price_american`. And `close_price_american` is empty on EVERY read, so
CLV and line movement remain unmeasurable (B32 unchanged). Only 418 of 1,641
reads carry any price at all.

WHY THIS BLOCKS THE TIERING WORK: every ROI number we compute is measuring
bookkeeping as much as picks. Tuning selection against it bakes the artifact
in. Price capture first, re-measure tiers second.

VERIFY: in `jerry_reads`, compare `price_american` against `price_best` where
both are non-null and count rows where the taken price implies a higher
probability than the best price did.

## Open, lower tier (logged so they are not re-discovered)

* **Side selection is not deterministic.** MIL @ SD 2026-10-06: `primary_play`
  computed HOME/Padres/95 at 12:58:44 and AWAY/Brewers/0 at 13:00:50 —
  opposite sides two minutes apart, both ending COVERAGE for different reasons
  (LR dissent, then the MC hard block).
  VERIFY: `primary_play_computed_at` against `primary_play.side` across a day.
* **`projected_spread` and `jerry_pred_spread` disagree in sign on 22.5% of
  games** (20 of 89). Directional accuracy against actual home margin:
  projected 66.3%, close_spread 57.3%, jerry_pred 52.8%,
  **model_pred_spread 49.3% (a coin flip, n=75)** — and both weak ones feed
  side selection. Leakage can only inflate, so the bad numbers are the
  trustworthy ones.
* **`mc_p_away_win + mc_p_home_win = 0.892`**, not 1.0 (MIL @ SD). MC gates
  every pick through `defensive_gates`.
* **Sharp displays a tier derived from base conviction next to a number taken
  from refit.** `refit_conviction` lands in a different band than base
  **59.3%** of the time (n=214, median gap 18.3, max 69.6). That is how a
  PRIME shows conviction 24.
* **`refit_conviction` covers 3.8% of the board** (386 of 10,202 since 09-22):
  100% on er/ks, **0% on every high-volume batter family** (rbis, runs,
  total_bases, hr, hits). Flat ladder. It is a second probability score, not a
  value score — decide whether it lives or dies.
* **102 published grades are provably wrong** — MLB 87 of 5,058 judged, NFL 15
  of 1,337. Correcting them makes the record **~11.8u WORSE**. ANDY'S CALL,
  untouched. NFL cause: `resolve_nfl_props.py` took the player's latest row of
  the season instead of the game being graded; disabled 09-19, rows never
  corrected.
  VERIFY: `python mlb_pipeline/audit_prop_grades.py --sport MLB --since 2026-06-01`
* **`daily_degen` is 25-119 (17.4%)** on n=144. Fix or retire.
* **`nfl_game_context.updated_at` is never written** — it is an INSERT
  timestamp, so row freshness is unknowable. `team_tendencies_updated_at` is
  NULL on every row. One line each in two files.
* **CFB "this week" tab shows 2 games** against a full Thu-Sun slate while NFL
  advanced correctly.
* **KenPom key must be rotated before NCAAB opens 2026-11-03** — it shipped
  inlined in builds up to v1.0.1 and is extractable from those binaries.
  Config is now clean (`78709762`); the exposed value is not recoverable.
  Also delete the stale `EXPO_PUBLIC_KENPOM_KEY` secret from GitHub and all
  three EAS environments — no code reads it.
* **EPA from ESPN play-by-play FAILS its pre-declared bar**: r=0.8593 against
  0.95. The EP surface is textbook and the error budget points at EP bin noise
  (fitted on 22,955 plays); three seasons (~400k) are now cached for a refit.
  `off_power_success` r=0.84 — not fit to use.
* **371 past-game ungraded receipts**, of which 238 are permanently
  unsettleable (R1). The rest need the settler run over a wider window.
* **Price thresholds are hardcoded across 10+ files** — `compute_surface_records`
  42, `defensive_gates` 36, `generate_sweat_card` 26, `generate_sharp_card` 20,
  plus two dedicated gate scripts. Every one approximates a price term missing
  from the ranking. They are why the decision path feels like it flip-flops.

## Closed 2026-10-06

| item | SHA |
|---|---|
| Sweat Card takes side+tier from `primary_play`, refuses to publish over a pass | `4656e20b` |
| `analyst_facts` names the team the spread favours (root cause of the Brewers prose) | `2b58f37a` |
| Pin guard: never delete a `prop_jerry_reads` row an ungraded receipt needs | `6e24d001` |
| Pin guard: same for `mlb_pipeline_props`, by composite key | `1d13e37a` |
| `settle_prop_receipts` covers every sport (was silently MLB-only), 45d window | `6e24d001` |
| `audit_prop_grades.py` — grade auditor independent of the grader | `e35a7bad` |
| `stat_pit_vs_cover.py` — point-in-time stat-vs-ATS engine | `45b099fc` |
| ESPN NCAAF volumetric stats, validated over 4 weekends | `e02efae1` `5b3d8222` |
| Advanced metrics from ESPN play-by-play, 7 of 8 at r>=0.97 | `f672aad1` |
| EPA model plus an honest failing verdict | `f9c03447` |
| Chain failure output persisted instead of discarded | `df3b2ee8` |
| Watchdog: context row stale when its slate is imminent | `1cc8737c` |
| `kenpom_pull` accepts only the server-side key name | `78709762` |
| CFBD key rotated by Andy, verified live, 376 stat rows upserted | 2026-10-06 |

## DISCUSSION 2026-10-07 · Split-tab replacement: fade-the-public ROI tracker

Andy's idea: replace Split with a fade product — track sides carrying a high
share of public TICKETS, publish the ROI of fading them, and surface today's
plays meeting the criteria. Cites NFL teams at >70% of bets going 3-12.
Separately: move Sweat Rating / Poll into Jerry (Jerry as an AP-voter style
ranker), freeing Split for this.

TESTED AGAINST public_splits_archive (125,728 rows, 5,337 latest-capture
game/market/side combos joined to results). Public side holding >=70% of
tickets:

    NCAAF  ML   public 84.6-92.6%   fading it 7-15%     <- catastrophic
    MLB    ML   public 58.4-61.8%   fading it 38-42%    <- catastrophic
    MLB    RL   public 39.6-47.2%   FADING 52.8-60.4%   <- the real signal
    NCAAF  RL   public 40.7-55.6%   fading 44-59%       <- mixed

THREE CONCLUSIONS

1. SPREAD/TOTAL ONLY. Fading moneylines would be a disaster: the public backs
   heavy favourites and favourites win games. That is the public being right
   at a bad price, and a hit-rate fade cannot see the price. The loss is in
   the juice, not the side.
2. ON THE SPREAD THE IDEA HOLDS. MLB run line fade returns 52.8-60.4% against
   a 52.38% breakeven, n=134-214 on the best-populated source.
3. WE HAVE NO NFL SPLIT DATA AT ALL. Zero NFL rows at any threshold, which
   matches the logged gap (cleatz + fadereport return 0 NFL; NFL has 2
   external sources against MLB's 13). The sport the 3-12 stat comes from is
   the one we cannot measure. An NFL fade tracker needs that pipe first.

CONDITIONS IF BUILT: spread/total only; publish ROI not hit rate (a record
without a price is what made the Degen parlay look like a disaster when its
3-leg form returns +9.5%); and it is structurally honest in a way our own
surfaces are not, since it grades someone else's picks.

VERIFY: public_splits_archive joined to {sport}_game_results on game_id, take
the latest capture per (game, market, side), threshold on *_bets_pct.

### UPDATE 2026-10-06 — The Fade shipped, and three statements above are now wrong

Built as `mlb_pipeline/compute_fade_records.py` (`462de172`). Three corrections
to the block above, all from auditing which column actually holds what:

**"WE HAVE NO NFL SPLIT DATA AT ALL" — wrong, and Andy caught it.** 19,512 NFL
split rows existed. My join returned zero because the ids are in two formats
with no overlap: splits key NFL on a 32-char hash
(`fc362aff0d889ec52d358307a70c32ed`), `nfl_game_results` on
`season_week_away_home` (`2024_18_SEA_LA`). `nfl_game_context` carries both and
bridges it. Reporting "no data" instead of asking why a join was empty is the
same failure as the NHL props that cannot reach their own game.

**The per-source numbers above are mostly sharp-side readings, not public.**
`archive_public_splits.py` keys `latest_fr`, `latest_ftp` and `latest_cz` on
`sharp_side_norm`, and only `latest_oc` on the real `pick_side`. So a ≥70%
reading on three of the four sources means *sharp and public agree*, not *the
public is piled in*. `cz` is worse — it reads
`cleatz_signals.sharp_bets_pct`, which is exactly 100.0 on 15.2% of its rows
and 0 never, while the other three never hit 100% in 139,188 values. The Fade
therefore reads **oc only**.

**Recomputed on the correct column**, ATS, ≥65% of tickets, graded −110:

    MLB    176-147  54.5%  +4.0% ROI  n=323
    NCAAF   31-26   54.4%  +3.8% ROI  n=57
    NFL      3-2    60.0% +14.5% ROI  n=5   <- not publishable alone
    NHL      no qualifying games

Conclusion 2 above survives in substance (MLB spread fade clears breakeven) but
at +4.0% rather than 52.8-60.4%, and NFL is n=5 rather than a product.

**Conditions from the block above that are now met:** spread only (ML excluded
in code with the measured reason in the docstring); ROI published next to the
record; grades someone else's picks.

**Still open:** `n` must appear beside every figure in the UI — NFL n=5 cannot
render as "+14.5%" unqualified. And the tab design itself.

---

### 🚨 NCAAF Games tab shows 2 games — client week arithmetic, needs a build

Not a data gap. `ncaaf_game_context` has 58 games for 10-06..10-10 (46 on
Saturday), all with `close_spread` and `primary_play`; the Odds API returns 67.

Shipped v1.0.2 derives the CFB week as 7-day blocks from a 2026-08-27 anchor.
On Tue 10-06 that boundary lands on **Thursday**, so 10-07 reads week 6 while
10-08/09/10 read week 7 and fail the `gwk === thisSeasonWk` filter. Exactly 2
games survive — the number Andy reports.

Fixed in `91c97750` (reads `ncaaf_game_results.week`); same filter then keeps
**59**. The app's anon key can read that table's `game_date,week` (228 rows,
all populated), so RLS is not in the way.

**BLOCKER: there is no OTA path.** `expo-updates` is not installed and
`app.json` has no `updates` block, so this client-side fix cannot reach a device
without a new binary. Two decisions for Andy: (a) ship a build, and (b) whether
to add `expo-updates` so client logic fixes stop requiring App Store review.

VERIFY after build: NCAAF Games tab on a Tue/Wed lists the Saturday slate.

---

# 2026-10-07 — line integrity, money flow, and tier caps

Found while building the NFL week-5 hand-by-hand. Seven items. One shipped,
one needs a migration applied by hand, five are open. House rules apply: no
line numbers, every item carries a VERIFY, nothing closed without a SHA.

**The two that block everything else: B51 is shipped but B57 refuses it at
the database, so the stale lines are STILL LIVE on the cards right now.**

### B51 · `close_spread` is the OPENING line, so we publish stale picks — **CLOSED** `9527924c` + `ba08482b`, run 2026-10-07

**ROOT CAUSE for the whole class.** `<sport>_game_context.close_spread` is in
practice the open, never refreshed:

| sport | rows with both | identical to `open_spread` |
|---|---|---|
| NFL | 314 | 263 — **83.8%** |
| NCAAF | 486 | 336 — 69.1% |
| MLB | 80 | 74 — 92.5% |

Every published number derives faithfully from it — `primary_play.line`, the
label, `jerry_reads.call_line`, the frozen receipt. So the pick builder was
never buggy; its input was the wrong column. A model projection was the
obvious suspect and is **rejected** (matches `projected_spread` 3 of 14,
`v3_spread` 2 of 14 — noise).

Not a timing problem either: `line_history` had all 14 of next week's games
priced at 10-06T19:56, before the reads ran.

The hole was CADENCE. Lines pull Tue 11am; the 6-hourly injury cron covered
**Thu–Sun only**. Nothing ran Tue to Thu, which is exactly when mid-week
injury news lands. BAL @ ATL opened BAL -6.5, traded -6.0 on 138 captures, and
is now BAL **+3.5** — 9.5 points with the favourite flipping — entirely inside
that gap, and the card read "BAL -6" all week.

FIX SHIPPED: `refresh_market_lines.py` updates market columns only from
`line_history` (median across books, `MIN_BOOKS=3`, never a started game,
never `primary_play`, fails closed per game). Wired before the label
normalizer in `nfl_pipeline.yml` and before the recompute in
`ncaaf_pipeline.yml`. Wednesday added to the cron.

**STILL OWED:** the live correction, in this order:

    python refresh_market_lines.py
    python recompute_nfl_primary_play.py --labels-only --lookback 7 --days 10

VERIFY: `python audit_published_lines.py --sport NFL --live` shows
`NEVER TRADED` at 0.

### B52 · A wrong line freezes into a receipt permanently — OPEN, needs a pre-capture guard

`public_receipts.pick_line` is set-once via `trg_freeze_receipt_identity`, so
BAL @ ATL's captured `game_read` receipt stays at **-6** even after B51
corrects the card. It will grade against a number nobody could take. Records
are not to be repaired, so the only fix is to stop a bad line reaching
capture.

Two distinct failure modes, and the guard must treat them differently:

- **MOVED** — the books did trade there. BAL -6 on 138 captures. Legitimate;
  keep it, and surface the move.
- **NEVER TRADED** — the books never posted it. NE -8.5 on a game that ranged
  -4.0..-3.0 across 1,064 captures. Block this one.

FIX: in the capture path, reject a `call_line` that
`market_line.ever_traded()` says never existed, and log it rather than
publishing. Do NOT block on "differs from current" — that would suppress
every legitimately moved pick.

VERIFY: `python audit_published_lines.py --days 35`. NFL was ok 59.3% /
MOVED 15.4% / NEVER TRADED 25.3% on 91 checkable picks.

### B53 · The `cz` money-flow source is unusable — **CLOSED** `ea82c918`

Andy spotted "100 percent on over" on a college card. One source, three
separate failures. NCAAF splits for 10/07-10/09:

| source | values exactly 0.0 or 100.0 |
|---|---|
| `fr` | 0 of 42 — 0.0% |
| `ftp` | 0 of 64 — 0.0% |
| `cz` | 18 of 74 — **24.3%** |

1. **Saturation.** Wyoming@SJSU `total OVER 100%/100%`; also MO State@WKU,
   Iowa State@BYU, FSU@Louisville.
2. **Both sides at 100%** in the same game (MO State@WKU `ml HOME 100/100`
   and `total UNDER 100/100`) — the numbers are not shares of anything.
3. **It duplicates its `ml` row into `rl`.** NMSU@FIU: `ml AWAY 33%/69%` and
   `rl AWAY 33%/69%`, identical every game. So any "the spread money is on X"
   sourced from cz is really the moneyline number.

Already on file at 15.2% saturation; it is worse on college and still feeding
the product.

FIX: exclude cz from money-flow display and from read prompts until the
scraper is fixed; show per-source values rather than a blend so a saturated
source is visible instead of averaged in. `fr`/`ftp` are clean; `oc` is what
The Fade runs on.

VERIFY: count `oc/fr/ftp/cz` values equal to 0.0 or 100.0 in
`public_splits_archive` for the current week.

### B54 · A moneyline edge verdict is applied to a spread bet — **CLOSED** `ea82c918` + `4719f6b6`, applied 2026-10-07

`model_edge.edge_pp()` correctly refuses non-moneylines, and its own docstring
says comparing conviction to an ML price is meaningless for a spread. But the
ORDER re-introduces exactly that: `defensive_gates` computes the edge cap on
the **ML price**, builds `new_pp` as `type: ml`, and then the
heavy-favourite reroute rewrites it to `type: rl` and keeps the cap.

Live examples: FIU capped STRONG to COVERAGE on "model 55% vs 64% implied"
where 64% is the **-178 moneyline**, then published as a -6.5 spread. BAL @
ATL capped PRIME to COVERAGE on "68% vs 74% implied" from an ML of -278, then
rerouted to a -6 spread.

So we declare "no value" on the moneyline, move the bet to the spread, and
carry the moneyline's verdict onto a proposition we never evaluated. This is
burying plays: 13 of 57 NCAAF games this week sit at COVERAGE.

FIX: on reroute, either drop the ML-derived `_edge_cap` (the spread is
unevaluated, not bad) or produce a cover probability and re-evaluate. Do not
silently inherit.

VERIFY: count rows where `primary_play._heavy_ml_reroute` is set AND
`_edge_cap` is non-null.

### B55 · The reads take their line from a third place — OPEN

Worse than either table. Of 81 NFL reads where
`nfl_game_context.close_spread`, `nfl_game_results.close_spread` and
`input_snapshot.signals.close_spread` all exist:

    snapshot matches BOTH tables      35
    snapshot matches NEITHER          19   <- 23%, a number from somewhere else
    snapshot matches context only     14
    snapshot matches results only     13

This feeds the published `call_line` AND `model_deference.market_need()`, so
the deference gate sometimes compares model margins against a line from
nowhere.

FIX: find what populates `signals.close_spread` and point it at
`market_line.home_line()`.

VERIFY: re-run the three-way comparison; `matches NEITHER` should be 0.

**Related, same family:** `nfl_game_results.close_spread` is the trustworthy
column (agrees with the books 6 of 6 on games tested) and is what
`model_scorecard.py` reads — so **the measured records quoted on 10/07 are
NOT contaminated**. `nfl_game_context` and results differ by 3+ on 15 of 270
games (5.6%); a handful of context values (IND@WAS +16.5 when the books
ranged +3.0..+4.5 over 501 captures; ARI@SF -17.5 vs -9.0..-7.0) never traded
at ANY point, so staleness alone does not explain all of it. The app reads
context, so the app is the exposed surface.

### B56 · Four published college lines are 3+ points off — **CLOSED** by B51's refresher + NCAAF recompute, 2026-10-07

NCAAF `close_spread` itself is clean (82.1% exact vs the books this week,
zero games 3+ off), so these are pick-level, not market data:

| pick | tier | published | the books |
|---|---|---|---|
| **Florida -14.5** | **STRONG, conv 81** | -14.5 | **-11.5** |
| BYU -14.5 | LEAN | -14.5 | -10.5 |
| North Texas -24.5 | LEAN | -24.5 | -28.5 |
| Georgia -2.5 | PASS | -2.5 | Georgia **+1.5** |

Florida is the only one on a card as a recommended play, and it is the
highest-conviction college pick of the week at 3 points worse than available.

FIX: covered by B51's refresher plus the NCAAF recompute once both run;
Florida should be re-derived. Confirm it actually moves.

VERIFY: `python audit_published_lines.py --sport NCAAF --live`

### Measurement caveat recorded the same day — the model vote is a dog proxy

Not a backlog item, a rule for anyone quoting the model numbers. Grade a flat
take-the-dog baseline on the SAME games before calling a model vote an edge:

| sport | model vote | flat dog, same games | verdict |
|---|---|---|---|
| NCAAF | **56.0%** n=166 | 50.0% | real, +6pp |
| NFL | 55.8% n=52 | **55.8%** | identical — dog proxy, 93% dog |
| MLB | 58.4% n=572 | **59.1%** | worse than the baseline |

In NFL and MLB "defer to the models" is the same instruction as "stop taking
favourites" — the override takes the favourite 28 of 29 times in NFL (45.5%
on favourites vs 66.7% on dogs). NCAAF is the reverse: there the favourite is
the good side (55.2%, n=210). Also: MLB records graded at a flat -110
overstate, because its "dog" is a +1.5 runline priced -150 to -200.

### B57 · The pick lock freezes the PRICE along with the pick — **CLOSED** `fe16116e`, migration applied 2026-10-07

Found trying to apply B51's correction. The refresher updated 107 games'
market columns successfully, then the label normalizer was **refused on 10 of
11 NFL picks** and reported "repaired 11" anyway.

`enforce_pick_lock` compares `primary_play->>'label'`. That label bundles the
team WITH the number — `"BAL -6"` — so a line refresh is byte-for-byte
indistinguishable from flipping the pick to another team. Once Thursday stamps
`pick_locked_at`, the NUMBER is frozen for the week too.

Every week-5 game carries a stamp (2026-10-02..10-07). The single game with no
stamp, BUF @ LA, is the only label that updated. That is the proof.

This contradicts the lock's own stated intent — 20260926b says it exists so
mid-week ensemble drift cannot flip a pick, and the labels-only pass says it
"changes NONE of those: it only rewrites label and line to agree with the side
the lock already froze". Both are right. The trigger just could not tell them
apart, because it was handed one string carrying two facts.

Two further problems in the same place:

1. **`pick_lock_drift` does not exist.** PostgREST returns 400. So every
   refusal since 20260929c has been silent.
2. **The write reported success.** `patch_pp` returned True on any 2xx, and
   the trigger returns **200 with the unchanged row**. `updated_at` moved,
   so the lie survived inspection. `_pp_locked()` could not catch it either:
   `pick_lock.lock_active('NFL')` was False (Wednesday) while the trigger
   locks off the stamp — two layers, different rules.

FIX WRITTEN: `supabase/migrations/20261007a_pick_lock_freezes_side_not_number.sql`
compares `side`, `type` and the TEAM at the head of the label instead of the
whole string. Side/market/team change is still refused; a number-only change
passes. Everything stays frozen once `kickoff_utc` passes, number included,
because a settled price must never be restated. Creates `pick_lock_drift`.

Call-site fix shipped in the same commit: `patch_pp` now reads the row back
and compares the label, so it can never again count a discarded write. It now
correctly reports **"repaired 0 FAILED 10"**.

**OWED: apply the migration** (needs to be run by hand, like 20261004a), then
re-run:

    python recompute_nfl_primary_play.py --labels-only --lookback 7 --days 10

VERIFY: `python audit_published_lines.py --sport NFL --live` → the 10/11 games
show `ok`; BAL @ ATL reads `BAL +3.5`. Then
`select count(*) from pick_lock_drift` should be 0 for number-only changes.

**Until it is applied the stale lines are still live on the cards** — BAL −6
(books +3.5), NE −8.5 (books −3.5), CHI +2.5 (books −2.5).


## 2026-10-07 late — B51/B56/B57 CLOSED, result

    audit_published_lines --live        ok      MOVED   NEVER TRADED
    NFL   before                        28      14      23
    NFL   after                         49       0       0
    NCAAF before                        56      24       2
    NCAAF after                         72       8       2

39 picks corrected across both sports. Florida -14.5 (STRONG, conv 81) is now
Florida -11.5. BAL -6 is BAL +3.5. No new pick_lock_drift rows, so the lock is
letting a number through and still refusing a pick change.

Residual, both understood and small:
* NCAAF 8 MOVED — all 10/10 games whose recompute also wanted a SIDE change,
  so the trigger correctly refused the whole patch. Needs a labels-only mode
  for NCAAF (NFL has one; NCAAF does not).
* 2 NEVER TRADED — Notre Dame -10 at BYU, where only ONE book is quoting
  (-12.5) so the refresher's MIN_BOOKS=3 floor correctly declined to write.
  Failing closed, as designed.

### CORRECTION to B52 — the frozen receipt is mostly RIGHT

B52 said the frozen `public_receipts.pick_line` of BAL -6 "will grade against a
number nobody could take". Wrong. BAL -6 traded on **153 captures**. It was a
real, takeable number when we called it, so the receipt is an honest record of
the bet we made and must NOT be touched.

The receipt is only wrong for the NEVER TRADED class — NE -8.5 on a game that
ranged -4.0..-3.0. So the pre-capture guard should block **only** numbers
`market_line.ever_traded()` rejects, and must NOT block on "differs from
current". B52 narrows accordingly, and `public_receipts.pick_line` turns out to
be the line-at-pick store that the architecture below needs.

### THE FOUR NUMBERS A PICK NEEDS — 3 of 4 exist

Andy: "pull opening line, pull line right before game start and lock, keep
pulling inbetween to assess movement when pick was made."

| number | where it lives | state |
|---|---|---|
| opening line | `open_spread` | present |
| continuous in-between | `line_history` | present — ~870 captures per NFL game over 8 days |
| line AT PICK | `public_receipts.pick_line` (frozen by trigger) | present |
| line at KICKOFF, locked | nowhere we capture ourselves | **MISSING** |

The close only reaches us post-hoc from nflverse (`nfl_game_results`), which is
accurate but third-party and NFL-only. Nothing snapshots the final number at
kickoff for any sport. That is the long-standing B32 ("CLV is unmeasurable").

And the product half: we now hold every number needed to show
"opened -6.5 · we called -6 · now +3.5 · moved 9.5 to ATL" and we surface none
of it. That display needs an app build (see the NO OTA PATH item).


## 2026-10-07 night — B53 + B54 CLOSED, two new items

### B53 result
`UNRELIABLE_SOURCES = {"cz"}` in splits_v2_pipeline, excluded from the
averages and the agreement count but NOT from the data (still in
`sources_present`, rows still archived). On the real Wyoming@SJSU shape:
`bets_pct_avg` 75.0 -> 62.5 (cz was dragging it 12.5 points) and
`sources_agree` 3 -> 2, which had been setting `triple_confirmed` off a
garbage source. Drop it from the set when the scraper is fixed — it reads
`cleatz_signals.sharp_bets_pct`, the root of all three symptoms.

### B54 result — NCAAF 10/07-10/17

    tier       before   after
    STRONG         22      27
    LEAN           20      18
    COVERAGE       17      14
    PASS            8       8
    spread picks carrying an ML cap:  7 -> 2

Five picks freed from a verdict earned on a moneyline they are no longer:
Bowling Green -7, Utah State -5.5, San Jose State -6, Kansas State -7.5,
Ole Miss -7.5 — all COVERAGE -> STRONG. The last 2 are games that had already
kicked off and were correctly skipped.

Took two passes. The first fix was INLINE in the reroute, which returns early
on a pick that is already 'rl', so rows rerouted on an earlier run could never
reach it — 5 of 7 stayed stuck. Extracted to
`drop_ml_edge_cap_on_spread(pp)`, which runs on any pick and handles fresh and
stale rows identically.

### B58 · apply_pick_gates_post_pass is in NO workflow — **CLOSED** `6897592c`

This is why the 5 stale rows sat there: the only tool that applies gate fixes
to STORED picks is manual. `grep -rn apply_pick_gates_post_pass
.github/workflows/*.yml` returns nothing. So every gate fix shipped since it
was written reaches new picks only, and any pick already written keeps the old
behaviour until someone runs it by hand.

Running it on NCAAF tonight changed 16 rows — 5 were B54, and 11 were other
gates that should have applied already (Miami (OH) ML and Wake Forest ML
LEAN -> COVERAGE, plus field-only fixes). That is 11 picks that were wrong for
an unknown number of days.

FIX: wire it into nfl_pipeline.yml and ncaaf_pipeline.yml after the recompute.
It is reasonably safe by design — it refuses promotions EXCEPT for rerouted
and unpriced picks, and skips started games — but adding an unwired tool to a
cron is a standing behaviour change, so it wants a decision rather than a
quiet commit.
VERIFY: `python apply_pick_gates_post_pass.py --sport NCAAF` (dry by default)
should report `CHANGED 0` the day after it starts running on schedule.

### B59 · ~450 games have no locked closing line — **DONE to the data ceiling** `6897592c`

`freeze_closing_lines.py` is exactly the right mechanism — T-5min MLB,
T-15min NFL/NCAAF, idempotent, stamps `close_locked_at` — and it IS on a
10-minute cron in prop_close_freeze.yml. But it is stamped on only 16 of 272
NFL rows (5.9%), 57 of 478 NCAAF (11.9%) and 0 of 81 MLB. Its own dry run
reports `NFL: 92 unlocked game(s) older than 12h` and `NCAAF: 358`.

It refuses to guess a close after the fact, which is correct. But we can now
RECONSTRUCT it: line_history holds every quote, so the last capture before
kickoff IS the close, and `market_line.index_by_matchup(rows, as_of=kickoff)`
returns it. That recovers CLV on ~450 games.

NOT DONE AND NOT TO BE DONE QUIETLY: writing `close_spread` on settled games
restates prices and would change grades. Andy's call.
Also worth checking why the live cron misses so much — the windows are
`*/10 22-23`, `*/10 0-4` daily and `*/10 16-21` Sat/Sun only, which look like
they should cover the NFL and college slates.


## 2026-10-07 late night — B58 + B59 done

### B58 · post-pass wired and applied
Applied NCAAF 16 rows and NFL 53 (40 COVERAGE->STRONG, 9 LEAN->STRONG, 4
field-only). Wired into both pipelines after the recompute, before the line
audit. Andy chose the STRONG ceiling over LEAN.

NOT flip-flopping, verified: a second run on both sports reports CHANGED 0, so
it converges. It refuses promotions except for rerouted/unpriced picks and
skips started games.

Also fixed its own output lying twice — `changed[:12]` hid 41 of 53 changes
(now `--limit`, default 80) and the "… and N more" trailer stayed hardcoded at
12 so a run that printed all 53 still claimed 41 were hidden.

### B59 · closing lines backfilled, and the ceiling found

    sport    rows written   close_locked_at coverage
    MLB            77       0.0%  ->  95.1%
    NCAAF         120      11.9%  ->  36.6%
    NFL            17       5.9%  ->  12.1%

ZERO grading impact, and the reason is worth keeping: the backfill writes the
CONTEXT table, while graders and `model_scorecard` read
`<sport>_game_results.close_spread` / `spread_result`. Only two games moved
more than 3 points and both were already-wrong numbers:

    ARI@SF   2026-09-27   context +17.5 -> +7.5   (11 books)
    NT@Tulsa 2026-10-01   context  -1.5 -> +3.0   (11 books)

and the Tulsa pick is a MONEYLINE, which cannot grade off a spread. On that
game `results.close_spread` was 2.5, near the books' 3.0, while context held
-1.5 — results was right there too, same as the NFL pattern.

**THE CEILING, and it is not fixable.** 75 NFL and 240 NCAAF games remain
unlocked because `line_history`'s earliest spread quote for every sport is
**2026-09-23**. A game that kicked off before then has no stored quotes, so
its close is genuinely unrecoverable — an absent record, not a join bug. Those
games stay unlocked and honest rather than being given a reconstructed number
that nothing supports.

Going forward `freeze_closing_lines.py` owns it: its dry run now finds and
freezes in-window games (2/2 NCAAF on the last check), and
`refresh_market_lines.py` refuses to touch any row with `close_locked_at` set,
so a verified close cannot be overwritten.

### The four numbers a pick needs — all four now exist

| number | where | state |
|---|---|---|
| opening line | `open_spread` | present |
| continuous in between | `line_history` | ~870 captures per NFL game over 8 days |
| line AT PICK | `public_receipts.pick_line`, frozen by trigger | present |
| close at KICKOFF, locked | `close_locked_at` + freezer, now backfilled | present from 2026-09-23 on |

What is still NOT built is the DISPLAY: nothing surfaces "opened -6.5 · called
-6 · now +3.5 · moved 9.5 to ATL" even though every number for it is now
stored. That needs an app build (see the NO OTA PATH item).


## 2026-10-07 · verify_backlog re-run — the list as it actually stands

`python mlb_pipeline/verify_backlog.py`. The file's own rule 4 says re-verify
before quoting it; this is that pass.

### STALE — resolved, text was out of date (4)
* **B10** NFL prop projection — now populated on **2690 of 2692** (backlog
  still said 2 of 1423).
* **B26** markdown in short_read — **0 of 473** forward-slate reads carry it.
* **B46** NHL reads with no reasoning — **134 reads, 0** engine-sub shorts,
  0 with a long_read under 300 chars.
* **NHL launch-date promise** — 0 of 134 reads claim a date, 0 carry the
  "27season" typo.

### STILL TRUE (3 machine-checked)
* **B44** NFL externals: 7 distinct sources vs MLB's 13.
* **B47** NFL prop publish gate: 149 publishable, {STRONG 141, PRIME 8},
  **LEAN absent** — the gate is unchanged, still shipping the losing tier and
  hiding the winning one.
* **B32** CLV — see the split below, because tonight only fixed half of it.

### B32 SPLITS IN TWO, and the halves are not alike

**Half done — GAME closing lines.** Fixed tonight (B59): `close_locked_at`
coverage MLB 0 -> 95.1%, NCAAF 11.9 -> 36.6%, NFL 5.9 -> 12.1%, reconstructed
from `line_history` back to its 2026-09-23 floor.

**NOT done, and NOT RECOVERABLE — PROP closing odds.**
`mlb_pipeline_props.close_over_odds` is populated on **71 of 36,790 rows**
(0.2%). And unlike game lines there is nothing to reconstruct from:

    prop_line_history        404 — does not exist
    prop_odds_history        404 — does not exist
    mlb_prop_line_history    404 — does not exist
    line_history markets     {total, spread, ml} — GAME markets only

So we have never stored a prop price over time. Every day without forward
capture is a day of prop CLV permanently lost, and it directly blocks the
one open analytical question in B36 — whether prop edge is real — because
you cannot measure a prop edge against a closing price you do not have.

`freeze_prop_closing_lines.py` is correct and does fire (it reports "no games
in freeze window" outside T-5/15min, which is right). The gap is window
coverage, the same disease the game freezer had: crons are `*/10 22-23` and
`*/10 0-4` daily plus `*/10 16-21` Sat/Sun only, which leaves weekday
afternoons (17:00-21:59 UTC) uncovered — exactly when MLB day games and
weekday college/NFL games start.

### B60 · Prop closing odds: 0.2% captured, nothing to backfill — **cron fixed 2026-10-07**, capture still to verify
FIX: widen the prop-freeze cron to cover weekday afternoons, then verify
`close_over_odds` climbs off 0.2% within a week. Consider a `prop_line_history`
table so a missed window stops being unrecoverable.
VERIFY: count `mlb_pipeline_props` rows with `close_over_odds` not null,
by game_date, and watch it rise.
WHY IT IS FIRST IN LINE: it is the only item on this list where waiting
destroys data that cannot be recovered later.

### NOT MACHINE-CHECKABLE (13) — unchanged
B1, B2, B5, B25 are build gates. B8, B9, B20, B21 are screenshot QA.
B3 is closed by Andy's decision (do NOT change records). B4, B29 are model
work. B40 is a workflow refactor. B41 needs a migration.


## 2026-10-07 · B44 worked, and the answer is not "port all five"

Verified first. Active sources since 10/01, narrower than the backlog's
3-day sample said:

    MLB 11   NCAAF 6 -> 7   NFL 6 -> 7   UFC 1

### DONE · sbr added to NFL and NCAAF (`d0977c18`)
Took it first because it is the best performing of the six missing — **56.8%
on n=750** — and was already a thin wrapper. Only its URL was MLB-specific;
`SBR_URLS` is now keyed by sport and `fetch_sbr` takes `sport=`.

Football consensus arrives on the SPREAD, not the moneyline (MLB publishes ML
with spread 0/0; NFL and NCAAF the reverse) and the existing emit gates handle
that with no branch. Live: NFL `rl HOME 62% / total OVER 61%`, NCAAF `rl AWAY
61% / rl HOME 61% / total OVER 64%`.

Caught on the way: `_emit_lean` hardcoded `source_url: SBR_URL`, so every
football pick would have been stamped with the BASEBALL page.

### THE RECORDS SAY DO NOT ADD TWO OF THEM

    sbr          56.8%  n=750   <- added
    vsin         53.4%  n=234   <- worth having, see B61
    oddscrowd    52.0%  n=3280  <- dead on football, see B62
    tonyspicks   51.0%  n=49    <- n too small to judge
    docsports    49.5%  n=757   <- BELOW breakeven (52.38%)
    betfirm      47.9%  n=265   <- BELOW breakeven, and no NFL page exists

**docsports and betfirm should NOT be ported.** They would add noise to the
consensus panel rather than signal, and handicapper picks as a voter block
already grade 46.6%. More sources is only valuable where the source clears
the price.

### B62 - ~~oddscrowd football is dead on THEIR side~~ **RETRACTED + FIXED**
NFL last produced 2026-09-20, NCAAF 2026-09-26. **MLB is healthy** (12 picks
today), so not a site-wide break and not our parser.

Root cause: `oddscrowd.com/games/upcoming/football` **is soccer.** Every game
link on it is Bundesliga, La Liga, Serie A, Premier League or Ligue 1. The
puller passes `sport_url_slug='football'` and has been reading a soccer
listing.

Probed american-football, americanfootball, nfl, ncaaf, college-football,
football-nfl, gridiron — all soft-404 (200 with zero game links) — and
oddscrowd's own navigation now links only `baseball`. There is no American
football slug to point at.

Nothing to fix until oddscrowd serves those pages again. Recheck
occasionally; do NOT rewrite the parser, because it is not the parser.

VERIFY: `python pull_externals_nfl.py --source oddscrowd --dry-run` — it
currently reports 0 picks from 23 fetches.

### B61 - vsin was keyed on a BYLINE and went dead - HALF FIXED
`fetch_vsin` required the string `peterson` in the article href or title.
VSiN changed columnist; the MLB best-bets column is now **Adam Burke's**.
Measured on the live landing page:

    sport-path + "best bets" + "peterson":   0 links
    sport-path + "best bets":               21 links

So it returned `[], 200` — a silent success — and vsin produced ONE pick in
all of October, on a 53.4% source.

FIXED: discovery no longer depends on a person's name. It matches the sport
path plus "best bets", takes the newest, and warns when it finds nothing. The
sport path was always the real guard against matching his college-basketball
columns; the name never was.

STILL BROKEN: the prose parser. Burke's column contains the string
"Moneyline" **zero times**, where Peterson's format was `TEAM Moneyline
-ODDS`. vsin still yields 0 and needs a parser written against the new format
— 24,875 chars of article text, `Pick:` appears twice, `ML` 66 times but
mostly site navigation, so the article body has to be isolated first.

**LESSON: never key a scraper on a byline.** A person's name is the most
volatile thing on a publisher's page and the least load-bearing. And a
fetcher returning `[], 200` on no-match reports success while dying — the
same silent-failure shape as the old pickdawgz NFL stub.

NFL and NCAAF vsin is a bigger job than MLB's: those columns are WEEKLY and
come from three cappers (Makinen, Reynolds, Youmans), not one daily writer.

### Remaining B44 candidates, honestly ranked
1. **B61 vsin parser** — 53.4% n=234. MLB first (one column), football after.
2. **tonyspicks** — NFL and NCAAF pages confirmed live, but needs a URL per
   sport, a team normaliser per sport (it uses `_mlb_norm`), AND a new
   action-to-market map, because its MLB map is baseball prose ("Lays the
   First-Five", "Lays the Run"). n=49 means we cannot yet say it is worth it.
3. **bettingpros** — NFL page live, but no graded record at n>=30, so adding
   it is a bet on an unmeasured source.
4. docsports, betfirm — **declined on the measurement.**


## 2026-10-07 · B62 RETRACTED — oddscrowd was never dead, it was three of our bugs

Andy: "What do you eman oddscrow dis dead,
https://oddscrowd.com/games/upcoming/ncaaf this has all data and there is an
nfl tab too". He was right.

**How I got it wrong:** I probed with `?hide_leagues=1` appended — because
that is what our puller sends — and counted only links matching one narrow
regex. Both choices hid the answer.

    /games/upcoming/ncaaf                  10 best-odds links, all -ncaaf-
    /games/upcoming/ncaaf?hide_leagues=1    0 links
    /games/upcoming/nfl                    10 best-odds links, all -nfl-
    /games/upcoming/nhl                    10 best-odds links, all -nhl-
    /games/upcoming/football               mixed NHL + NCAAF catch-all

### The four bugs
1. **`?hide_leagues=1` empties the page.** Removed from `LIST_URL`.
2. **`football` and `hockey` are catch-all feeds, not leagues.** NFL/NCAAF
   now use `nfl`/`ncaaf`, NHL uses `nhl`. **MLB deliberately stays on
   `baseball`** — `/upcoming/mlb` names the teams in page text but exposes
   only 3 `/games/` hrefs, so its per-game links are client-rendered. Same
   for `/upcoming/nba`.
3. **A weekly sport against a daily date filter.** `accepted_dates` was
   {today, tomorrow}. Verified on CHI @ GB that title, anchor and all three
   market regexes matched with usable splits — then the page was discarded
   for being dated Oct 11. `lookahead_days` added (default 1 so MLB is
   unchanged); football passes 8.
4. **`pull_id` was the wrong column** — surfaced only once NHL started
   returning picks. `external_pull_log` has an integer `id` AND a uuid
   `pull_id`; `start_pull_log` returned the integer and `write_picks` stamped
   it into a uuid column. Invisible while the fetcher returned zero, because
   `write_picks` short-circuits on an empty list. **A pull that writes
   nothing cannot reveal a broken write.**

### Result, written live
    NFL     oddscrowd  0 -> 39 picks / 13 games
    NCAAF   oddscrowd  0 -> 57 picks / 19 games
    NHL     oddscrowd  0 -> 20 picks /  7 games

Active sources today: **NFL 6 -> 7, NCAAF 6 -> 7, NHL 0 -> 1**. oddscrowd is
52.0% on n=3280, the largest sample of any source we carry.

NBA and NCAAB remain unverified — `/upcoming/nba` exposes no static links and
NCAAB has not started. **Recheck NBA at the 10-21 open.**

VERIFY: `python pull_externals_nfl.py --source oddscrowd --dry-run` should
report ~39 picks; `--source oddscrowd` for ncaaf ~57; nhl ~20.


## 2026-10-07 · B60 — the freeze window was missing one hour, and it cost 81 games

Measured every game with a kickoff time since 09-01 (n=943) against whether
the fast cron is awake in the ~10 minutes before it, which is what a
T-5/T-15 freeze actually needs:

    sport   games  freezable  UNCOVERED
    MLB        32     24          8
    NCAAF     483    400         83
    NFL       272    261         11
    NHL       156    153          3
    ALL       943    838        105    = 88.9% covered

**81 of the 105 sat at 15:00 UTC, every one of them a Saturday.** Those are
college NOON ET kickoffs (16:00 UTC) whose T-10 falls at 15:50 — ten minutes
before the weekend window opened at 16:00. The entire early CFB slate, every
week, lost to one missing hour.

### Changed
    */10 16-21 * * 0,6   ->   */10 15-21 * * 0,6     +81 games, +12 runs/wk
    added                     */10 19-21 * * 1-5     +14 games, +90 runs/wk

Coverage 88.9% -> **98.9%**. Simulated before editing:

    schedule                    covered   gap      %   runs/wk
    current                         838   105   88.9%      366
    weekend 15-21                   919    24   97.5%      378
    weekend 15-21 + wd 19-21        933    10   98.9%      468
    weekend 15-21 + wd 13-21        937     6   99.4%      648

### DELIBERATELY LEFT UNCOVERED
The last 10 games sit at 13:00, 14:00, 16:00 and 17:00 UTC on weekdays.
Covering them needs `*/10 13-21 * * 1-5` — **180 more runs a week for 4 more
games.** Declined on the ratio, recorded here rather than silently accepted.

### WHICH FREEZER THE CFB RECOVERY HELPS
NCAAF carries no props by project rule, so those 81 games land entirely on
`freeze_closing_lines.py` — GAME closing spreads and totals, which is what
CLV on sides is measured against. The prop freezer in the same step gains
from the MLB and NFL hours, not the Saturday one.

### STILL OWED on B60
Prop closing ODDS remain at 71 of 36,790 rows (0.2%) and **cannot be
backfilled** — `prop_line_history`, `prop_odds_history` and
`mlb_prop_line_history` all 404, and `line_history` carries game markets only.
So this fix only helps from today forward. VERIFY in a week: count
`mlb_pipeline_props` rows with `close_over_odds` not null, grouped by
game_date, and confirm it climbs off 0.2%.

If it does not climb, the next step is a `prop_line_history` table so a missed
window stops being permanent — which is the real structural fix.


## 2026-10-07 · B47 CLOSED — the pre-registered test killed the candidate

Andy concurred with R3 on 09-24 *conditional on seeing the 09-27 weekend
first*. That window now exists, so the recorded verify was run:

    python audit_nfl_publish_rules.py --since 2026-09-27 --by-tier

1,151 graded NFL props, 2026-09-27..10-05, labelled OUT-OF-SAMPLE by the
script itself (rule written 09-24 on data to 09-21).

    R1 live today: PRIME+STRONG, any price, any stat  54.5%  ROI +3.14%  +11.9u  n=380
    R2: PRIME+STRONG, in band, counting only          55.2%  ROI +5.92%  +11.9u  n=201
    R3 CANDIDATE: +LEAN, in band, counting only       52.4%  ROI +0.36%   +1.3u  n=359
    R4: PRIME+LEAN (no STRONG), in band, counting     49.1%  ROI -6.03%   -9.8u  n=163
    R6 price gate alone (any stat)                    53.9%  ROI +2.80%  +10.3u  n=369
    R7 counting alone (any price)                     53.3%  ROI +0.64%   +2.4u  n=381

    R3 vs R1:  ROI -2.78pp   units -10.6u   exposure -21 props

**R3 IS REJECTED.** Shipping it would have cost 10.6 units over nine days.

### The premise inverted, which is the whole lesson
R3 existed because LEAN measured as the largest and most profitable tier
in-sample (+5.08%, n=185). Out-of-sample, inside R3:

    PRIME     60.0%  ROI +15.30%   +0.8u  n=5     n<30
    STRONG    55.1%  ROI  +5.68%  +11.1u  n=196
    LEAN      48.7%  ROI  -6.71%  -10.6u  n=158

LEAN went **+5.08% -> -6.71%**. The backlog entry predicted exactly this
("both filters were chosen by looking at the same rows they score on...
in-sample selection always flatters") and writing the honest test down before
agreeing to ship is what caught it.

### R2 looks best and is NOT being shipped either
R2 beats live on the same units with half the exposure. But selecting it from
*this* window repeats the error one level up — it would be chosen by looking
at the rows that score it.

And its mechanism is already undermined: **the -150..+150 band is enforced at
source** since `33f90b12`, so it no longer discriminates. Measured:

    all tiers, before 09-24     1157/1361 priced rows in band   85.0%
    all tiers, from 09-24 on    1428/1475                       96.8%
    PUBLISHED tiers since 09-27  472/483                        97.7%

So R2's band filter excludes 2.3% of published props and cannot be producing
its advantage; the counting-stats filter would be doing all the work — which
makes "counting stats only" the hypothesis to pre-register, not R2 as bundled.

### DECISION: no change to v_nfl_props_publishable
R1 is performing out-of-sample (54.5%, +3.14%, +11.9u on n=380) and no
alternative is justified on evidence that was not selected on itself.

PRE-REGISTERED FOR THE NEXT WINDOW, written down before looking:
  * hypothesis: PRIME+STRONG restricted to COUNTING stats beats PRIME+STRONG
    on all stats, on ROI, at n>=150.
  * window: graded NFL props with game_date >= 2026-10-08.
  * command: `python audit_nfl_publish_rules.py --since 2026-10-08 --by-tier`
    and compare R1 against R2/R7.
  * ship only if it holds. If it does not, the gate stays as it is.

Also still true and unchanged: PRIME has effectively stopped occurring (n=5
in nine days), so users see STRONG and almost nothing else. That is a
labelling question — four tiers is more resolution than the calibration map
supports — not a money question, and it is parked rather than closed.

---

## B63 — 12 NHL receipts were born without a pick, and nothing noticed
**Found 2026-10-08** while answering Andy's question about the NHL
calibration banner. Open.

NHL grading looked stalled: `public_receipts` showed 0 of 12 graded for
10-06 and 10-07 while every earlier date was 100% graded. Score ingest was
fine (`nhl_game_results` holds all 14 finals) and the source rows were fine
— `jerry_reads` is 9/9 and 3/3 graded on those dates with real calls.

The receipts themselves are empty shells:

    GRADED 10-05 receipt     market='ml'    pick_label='Tampa Bay Lightning ML'
                             pick_side='HOME'  conviction=81  result='WIN'
    STRANDED 10-06 receipt   market='game'  pick_label=NULL
                             pick_side=NULL    conviction=0   result=NULL

`source_id` is correct on all 12 — each points at the right `jerry_reads`
row, and every one of those rows carries a complete, graded call (e.g.
id=6117 'Toronto Maple Leafs ML' conv 59 → Win). So the writer captured
identity (game_id, matchup, odds, published_at) and dropped the pick. All 12
carry `capture_mode='reconstructed'` and `published_at` on 2026-09-29, so
they came from one backfill run.

Consequences:
  * `grade_public_receipts` can never grade them — there is no side or market
    to resolve, so they sit in the ungraded bucket forever and look like a
    grading failure when they are a WRITE failure.
  * any record computed from receipts under-counts NHL by 12. This is why
    `refresh_calibration_notice.py` counts `jerry_reads` instead.

Two things to fix, and the second matters more:
  1. backfill the 12 from their source rows — but note `public_receipts`
     freezes identity and pick fields after publish (migration
     20261007a), so this likely needs the freeze path, not a plain PATCH.
  2. the writer must refuse to emit a receipt with no pick. A receipt whose
     `pick_label`/`pick_side` is NULL and whose `market` is the placeholder
     `'game'` is not a published pick and should either carry the pick or not
     exist. Add the assertion at the write, and a watchdog that fails on any
     receipt with identity but no pick.

## B64 — surface_records: NULL window_label and cross-sport contamination
**Found 2026-10-08.** Open.

`surface_records` for sport=NHL returns 22 rows with `window_label` NULL on
**every one**, and duplicate surfaces carrying different numbers:

    ledger          9-5 (n=14) · 8-5 (n=13) · 56-76 (n=132)
                    101-140 (n=241) · 106-140 (n=246)
    nhl_sides       2-10 (n=12) · 1-8 (n=9) · 4-12 (n=16) ×3
    sharp_card      2-4 (n=6) · 2-3 (n=5) · 6-6 (n=12) ×3
    prop_coverage   2080-1791 (n=3871)   <-- NHL has no prop record remotely
                                             this size; these are MLB numbers
                                             sitting under sport=NHL

`window_label` is what distinguishes 7d / 30d / season. With it NULL the app
cannot tell the rows apart, so whichever one it reads is arbitrary — which is
the same class of defect as the ledger record Andy caught surfacing 1-0 for
two days. And `prop_coverage` at n=3,871 under NHL is cross-sport leakage,
not an NHL record.

Needs: (a) why `window_label` writes NULL for NHL when the aggregator takes
a window argument, (b) whether `prop_coverage` rows are mis-tagged at write
or the sport filter is being ignored, (c) a uniqueness constraint on
(sport, surface, window_label) so duplicates cannot accumulate silently.

Do NOT fix by picking the row that looks right. Andy: "no problem solving is
actulaly happening jys band aid after band aid".

---

## B65 — MLB games exist TWICE, and the surfaces split across the copies
**Found 2026-10-08** from Andy: "SO i see CHW as POTD but CLE ML in game djery
pick and CLE in the sharp and CHW as Dawg of the day." Open. **Live impact.**

One game — Cleveland Guardians @ Chicago White Sox, 2026-10-08 — has **two
rows in `mlb_game_context`** under different `game_id`s, with the **same two
starting pitchers** (Hagen Smith / Parker Messick), so it is a duplicate and
not a doubleheader. 73 of 324 columns differ between them:

    gid e0448f  fetched 01:26   total 7.5  ML -112  conviction 72  PRIME
    gid b57e79  fetched 19:14   total 7.0  ML -115  conviction 55  LEAN
                                                    (MC dissent downgrade)

Same engine (`lr_v1`), same side (CLE AWAY), near-identical p_home_win (0.283
vs 0.274). The stale 01:26 row was never retired when the game was re-keyed.

**The surfaces then split across the two copies, and across two models:**

    POTD           Chicago White Sox ML   "resolver STRONG"   <- opposite side
    Dawg of Day    Chicago White Sox      STRONG              <- opposite side
    Game read      Cleveland Guardians ML LEAN 55  (fresh row b57e79)
    The Sharp      Cleveland Guardians ML PRIME 72 (STALE row e0448f)

Two defects, both real:

1. **The Sharp is publishing a PRIME the engine has since downgraded.** The
   fresh row's own `sub` reads "55% vs 53% implied — no edge". The Sharp is
   carrying PRIME/72 off the stale copy.
2. **POTD and Dawg disagree with the game read about who wins.** POTD's
   resolver likes CHW off starter xERA (Smith 2.45 vs Messick 3.15) and
   `projected_spread` +0.57 toward home. The game read's `lr_v1` has
   `p_home_win` = **0.274** — it thinks CHW wins 27% of the time. These are
   not two angles on a game, they are two models flatly contradicting each
   other on a moneyline, with no arbiter. A subscriber backing both the POTD
   and The Sharp is on both sides of one game, paying juice twice.

**It also explains the missing money flow.** All 19 split rows for this game
are attached to the STALE game_id (e0448f); **zero** to the fresh one the app
reads. So "MLB money flow not present" is the same root cause, not a separate
scraper gap.

Scope — MLB only, and recurring:

    mlb_game_context      4 of 71 rows since 09-01 duplicated (5.6%)
      x2 2026-09-25  Baltimore Orioles @ New York Yankees
      x2 2026-09-25  Chicago Cubs @ Boston Red Sox
      x2 2026-09-26  New York Mets @ Washington Nationals
      x2 2026-10-08  Cleveland Guardians @ Chicago White Sox
    nfl_game_context    0 · ncaaf_game_context 0 · nhl_game_context 0

Needs, in order:
  1. a unique constraint on (game_date, away_team, home_team) for MLB so a
     re-key cannot create a second row silently;
  2. a rule for which row wins when a re-key happens, and migration of the
     splits/receipts/read off the retired id — note the splits live on the
     STALE id, so "delete the old row" loses the money flow;
  3. an arbiter between the POTD/Dawg resolver path and the game-read
     `lr_v1` path, or an assertion that refuses to publish two surfaces on
     opposite sides of one moneyline. The second is cheap and should exist
     regardless of which model is right.

Do NOT resolve by picking whichever row looks better. Andy on exactly this:
"no problem solving is actulaly happening jys band aid after band aid".

---

## B66 — the registry hit_rate barely predicts reality (r=+0.20, 11pp error)
**Found 2026-10-08** from Andy: "then the leak inflated weights". Open.
Instrument shipped (`audit_signal_claims.py`); the fix is NOT shipped.

NCAAF's `signal_registry` advertises hit rates no ATS signal sustains —
`ncaaf_home_fav_week1_chalk` 94.3% (n=70), `ncaaf_sp_plus_edge_home` 91.2%
(n=68), `ncaaf_confluence_home` 85.6% (n=139), `ncaaf_home_field_baseline`
74.5% (n=376) — while NCAAF published reads run **50.8%**.

### Why the existing guard doesn't catch it
`ensemble_scorer.edge_weight_v2` runs hit_rate through a Beta prior centred on
breakeven. That protects against SMALL-SAMPLE luck and nothing else. For
`ncaaf_home_field_baseline` at 74.5%/n=376:

    posterior = (10.48 + 280) / (20 + 376) = 0.7335
    edge_pp   = 0.2095
    weight    = 1 - exp(-0.2095/0.06) = 0.97 of maximum

A leak-inflated claim with a LARGE sample earns near-maximum weight. Nothing
asks whether the claim is believable.

### The measurement
`primary_play._ensemble_sources` records every contributing signal with the
hit_rate and contribution it was given at scoring time. Joined to the graded
read, that yields each signal's rate ON THE PICKS IT DROVE — out of sample by
construction. NCAAF, 246 graded picks:

    signal                      claim        actual        gap
    ncaaf_confluence_home       85.6% n=139  63.6% n=11   -22.0pp
    ncaaf_home_field_baseline   74.5% n=376  57.1% n=28   -17.4pp
    ncaaf_projected_spread      73.9% n=207  64.3% n=14    -9.6pp
    ncaaf_home_spread_edge      62.2% n=45   40.0% n=20   -22.2pp
    ncaaf_home_underdog_bark    54.8% n=42   33.3% n=18   -21.5pp

    correlation(claimed, actual) = +0.204    (17 signals, actual n>=10)
    mean ABS gap                 = 11.0pp
    over-claims >5pp 7/17 · under-claims >5pp 4/17

### Why this reframes the fix
The registry number is not merely inflated — it is close to **uninformative**,
in BOTH directions (`home_ats_hot_at_home` claims 51.9% and delivered 70.4%;
`home_team_ats_hot_season` claims 44.8% and delivered 58.8%).

So a plausibility ceiling on the >60% claims is NOT the fix. Measured, it
would touch only **10.2%** of total NCAAF contribution (33.1 of 324.8) and
would do nothing about an 11pp average error. The inflated giants are also
low-volume — `ncaaf_confluence_home` drove 14 picks, `ncaaf_home_spread_edge`
8 — while the workhorses (`ncaaf_ol_weight_adv_home` n=108,
`ncaaf_ground_leverage_home` n=57) are much closer to honest at -2.6 and
-4.2pp.

NFL is not materially exposed: 1.2% of its contribution flows through >60%
claims, on 55 graded picks.

### The fix this points at — Andy's call
Feed the OBSERVED on-pick rate back as the weight input instead of the
backfilled hit_rate. The small samples that produces are exactly what
`edge_weight_v2`'s Beta prior exists to handle: it shrinks toward breakeven
until a signal earns otherwise. That is a genuine calibration loop grounded in
our own graded picks rather than in a backfill (`origin=BACKFILL_2026-10-08` on
102 of 110 NCAAF rows) whose provenance cannot be reconstructed.

Sequencing, deliberately: this is a suppression-gate-class change to how picks
are scored. It gets shadowed and decided on evidence, not shipped the night
before a slate locks. `audit_signal_claims.py` is the instrument; run it weekly
and the observed column becomes trustworthy as n accumulates.

Related: 162 of 191 NFL and 58 of 110 NCAAF registry rows are tier
UNVALIDATED and still carry weight; NCAAF has 17 ANTI_VALIDATED.

---

## B67 — UFC externals cannot be graded: the game_id is a foreign ID space
**Found 2026-10-08.** Open. **A naive fix here produces wrong grades that
look correct — read this before touching it.**

UFC externals: 63 picks, all from `bfo`, all `ml`, 46 on completed events,
**0 graded**. Two gaps looked like the cause: UFC is absent from
`resolve_externals.SPORT_CONFIG`, and no workflow calls the resolver for UFC
at all. Both true, and both irrelevant, because the join underneath does not
exist.

`ufc_fight_results` has everything needed (`fighter_a`, `fighter_b`, `winner`
as 'a'/'b', `method`, `event_date`). And `external_picks.game_id` is populated
on all 63. Joining them on `ufc_fight_results.id` even appears to work:

    external pick       game_id '388'   game_date 2026-08-01
    ufc_fight_results   id       388    event_date 2025-09-13

**Eleven months apart.** The match is coincidental — both ID spaces are small
integers, so 3 of 6 sampled ids "matched" and returned a real `winner`. A
resolver built on this would have silently graded picks against unrelated
fights and reported success. Same class as
`project_nhl_props_cannot_join_their_game_1003`.

`external_picks.game_id` for UFC is bestfightodds' own fight id (source
`bfo`). There is no fallback: the rows carry no fighter names, no matchup and
no pick_label — only `pick_side` ('FIGHTER_A'/'FIGHTER_B') and
`odds_american`. So the side cannot even be resolved to a person without the
bridge.

Needs, in order:
  1. an identity bridge from the bfo fight id to `ufc_fight_results` — most
     likely by capturing fighter names at scrape time in the bfo puller, then
     matching (event_date, fighter pair) the way `resolve_externals` does for
     team sports;
  2. only then a UFC path in the resolver. UFC genuinely does not fit
     SPORT_CONFIG's home/away + spread_result shape, so it needs its own
     grader, not a config entry (`feedback_universal_vs_sport_specific`:
     universal unless the data model differs — here it differs);
  3. a join-rate assertion so a 0%% or coincidental match rate fails loudly
     instead of writing grades.

Until (1) exists, UFC source records cannot render, and that is the honest
state rather than a bug to paper over.

DO NOT "fix" this by adding UFC to SPORT_CONFIG and joining on id.

---

## B66a — the reweighting shadow is BLOCKED: we only store the winning side
**Found 2026-10-08** while starting the shadow build B66 points at.
Prerequisite shipped; the shadow itself is still blocked until data accrues.

B66 established that `signal_registry.hit_rate` predicts a signal's real
performance at **r=+0.204** with an 11pp mean error, and that the fix is to
weight by the OBSERVED on-pick rate instead. That change alters how every pick
is scored, so it has to be shadowed before it ships.

**It cannot be shadowed from what we store.** Measured over 408 graded NCAAF
picks carrying `_ensemble_sources`:

    picks whose stored sources span MORE THAN ONE side : 0
    picks whose stored sources are all ONE side        : 408
    runner-up field on primary_play                    : none

Only the chosen candidate's contributions are persisted
(`top.contributions[:8]`). Reweighting can therefore move the winning side's
score but can never answer *would the pick have flipped*, which is the only
question that matters. Every entry does carry both `weight` and `contribution`,
so base strength is recoverable — the missing half is the losing side, not the
arithmetic.

Re-running the scorer over history is NOT a substitute: `team_stats_rolling`
is current-only, so a historical re-score reads post-game stats and leaks
(`project_rolling_stats_leak_trap_929`).

### Shipped: persist the runner-up
`MarketDecision.runner_up_contributions` and `.runner_up_side` are already
computed on every decision (ensemble_scorer:1778) and were discarded.
`_ensemble_runner_up` now stores them in the same shape as
`_ensemble_sources`, at all eight write sites — NCAAF, NFL, NHL, NBA, MLB and
the three recompute passes. Same precedent as `_ensemble_all_markets`, which
exists so the "what if we'd played spread instead of ML" question stays
answerable.

Note the accrual path: this populates on a game's FIRST publish (the lock
permits that — `OLD.primary_play IS NULL`), not on re-scores of already-locked
picks. So data builds one slate at a time regardless of the lock state.

### Still open
  * the shadow itself, once a few weeks of slates carry a runner-up
  * then the go/no-go on observed-rate weighting
  * 45 of 408 picks have stored sources whose side DIFFERS from the published
    pick side — the LR override and the ML/RL reroutes change the side after
    the ensemble chose it, so `_ensemble_sources` describes the pre-override
    pick. The shadow must account for that or it will compare the wrong thing.

## 2026-10-09 · B68 + B69 — Andy's two product items (DISCUSSION, not yet scoped)

Andy 2026-10-09: *"Thoguths on changinfg how stats are displayed to where its
Home O vs Away D, Away O vs Home D, instead of seeing offense then switching to
defense ot see both teams of each ... this will be discussion. Other thing i
wnated to bring up is abetter filtering process for prop jerry or better way of
surfcing in game prop loko in game details"*

### B68 · Stat display: matchup pairing instead of Offense/Defense sub-tabs — **SHIPPED 2026-10-19** `56651d86`

**Today:** `TeamStatsCard` in `app/components/GameDetailV2.tsx` holds
`side: 'off'|'def'` and renders rows as *stat label | away cell | home cell*.
So the Offense tab compares AWAY OFFENCE to HOME OFFENCE — two units that never
face each other. Answering "can the home team move the ball" means holding four
numbers across a tab switch.

**Andy's framing is analytically correct, not just a layout preference.** Points
are produced by Home O vs Away D. It is also the exact form of our own rating
model (`perf = off_i - def_j + home` in `opponent_adjusted_rating.py`), so the
display would finally mirror the engine instead of cutting across it.

**It is the same data transposed** — the sub-tab stops meaning Offense/Defense
and starts meaning *which team has the ball*. No new query. The work is in which
keys pair.

**HOW WELL EACH SPORT PAIRS** (from the OFFENSE / DEFENSE key lists):

- **NBA — best fit.** points_pg/points_allowed_pg, efg_pct/opp_efg_pct,
  tov_pct/opp_tov_pct, orb_pct/opp_orb_pct. Four-factors was designed for this.
- **NHL — excellent.** xgf_per60/xga_per60, high_danger_for/against, and
  pp_pct/pk_pct, which is a textbook special-teams matchup.
- **NFL — 5 clean pairs.** pass_yds_pg, rush_yds_pg, total_yds_pg,
  off_pass_epa/def_pass_epa, off_rush_epa/def_rush_epa.
- **NCAAB — 2 pairs.** ppg_for/ppg_against, off_rating/def_rating.
- **NCAAF — ONLY 3 pairs, and it is a DATA gap, not a UX one.** off_epa_per_play,
  off_success_rate and sp_offense pair; pass_yds_pg / rush_yds_pg /
  total_yds_pg / third_down_pct / off_explosiveness have NO defensive
  counterpart because the NCAAF_DEFENSE list carries no yards-allowed stats at
  all. A matchup view ships half-empty for NCAAF until those are pulled.
- **MLB — does not pair conceptually.** Batting (obp/slg/woba/wrc+) against
  pitching (era/whip/k9) are different units. MLB's real matchup is lineup vs
  the STARTING PITCHER, which is a different feature, not this one.

**THREE CATEGORIES, NOT TWO** — the structural point worth settling first:

1. **Matchup pairs** (Home O vs Away D) — drive each side's scoring.
2. **Game environment** — pace/tempo, and in NFL the wind finding. These are
   ADDITIVE, not opposed: both teams' pace combine to set total plays. Putting
   tempo in a "vs" column implies a contest that isn't happening.
3. **Context** — SOS/SOR, which tell you how much to trust 1 and 2. The 09-26
   comment in the file already says schedule context belongs beside the stats.

**ALSO FOUND:** the NFL_OFFENSE list has no points_pg while NFL_DEFENSE has
points_allowed_pg — so you can see what an NFL team concedes but not what it
scores. Asymmetric and probably just an omission.

**HONEST CAVEAT to carry into the discussion:** a matchup differential invites
"home O outranks away D, so bet home". Our own measurements say team stats do
not beat the close (project_models_dont_beat_the_close_1005, and the NCAAF
market is 50/50 at every slice on 6,329 games). Ship this as *comprehension*,
not as an implied edge — no green "EDGE" chip on the pair.

VERIFY: grep for `const [side, setSide]` and the `_OFFENSE = [` / `_DEFENSE = [`
lists in app/components/GameDetailV2.tsx
FIX: regroup the key lists into pairs + environment + context; relabel the
sub-tab. Needs a client build (project_no_ota_path_client_fixes_need_builds).

### B69 · Game-detail props: price is never shown, and Jerry's read is right there unused

Found while scoping Andy's second item. GamePropsPanel and its fetch in
GameDetailV2.tsx select only
player_name, player_team, prop_type, direction, prop_line, display_conviction,
tier, signals.

`v_mlb_props_publishable` has 21 columns. **Four relevant ones are already in
the view and simply not selected:**

- `book_over_odds`, `book_under_odds` — **THE PRICE IS NOT SHOWN AT ALL.** A
  user cannot see they are being offered -250 on a prop whose own publishable
  band is -300..+150, and cannot see the Batter Hits O0.5 -200+ juice trap.
  After a full day establishing that price decides the result, a prop row
  without its price is the single worst omission in the panel.
- `jerry_short_read`, `jerry_verdict`, `jerry_conviction` — Jerry's prose is
  sitting in the view unselected. Andy's "better way of surfcing in game prop
  loko" is mostly **selecting fields that already exist**, not new pipeline.

**Two silent-blank defects of the same class** (feedback_explicit_select_silent_blanks):

1. The panel renders `p.projected_value ?? p.projected` — **neither column
   exists in the view**, so "Projected" can NEVER render on the self-fetch
   path. It only appears when a parent passes gameProps in. Dead field in the
   modal.
2. Fetch is `.limit(15)`, panel is `.slice(0, 8)` — **7 props silently
   dropped** with nothing telling the user more exist. Same truncation class as
   project_postgrest_truncation_audit_912.

**And it is MLB-ONLY:** the fetch is gated on `gamesSport === 'MLB'`. NFL, NHL
and NBA props never appear in game detail. (NCAAF/NCAAB are correctly excluded —
feedback_college_sports_no_props.)

So "better filtering" has a cheap prerequisite: show price, show Jerry's read,
stop dropping 7 rows, and say how many were filtered. Filtering UI on top of a
panel that hides the price would sort the wrong field.

VERIFY: grep for `v_mlb_props_publishable` in app/components/GameDetailV2.tsx
and read the select list.
FIX: widen the select, render price with the band gate visible, group by prop
family, surface the filtered-out count. Client build required.

## 2026-10-09 · B64 CORRECTED — two of its three claims are wrong

Andy's note on B64 was *"no problem solving is actulaly happening jys band aid
after band aid"*, so this was re-derived from the tables rather than patched.
Two of the three claims do not survive, and the residual issue is a different
one.

**CLAIM 1 — "window_label NULL on every row" → COSMETIC, already retracted.**
`window_key` is the discriminator and it IS populated: d7 / d30 / mtd / epoch
/ lifetime on all 22 NHL rows. `window_label` is a redundant second column.
Nothing downstream is ambiguous.

**CLAIM 2 — "duplicate surfaces carrying different numbers" → NOT DUPLICATES.**
They are different TIME WINDOWS of the same surface, which is the table's
whole design:

    ledger     d7 10-4 · mtd 11-5 · d30 54-70 · epoch 103-140 · lifetime 108-140
    nhl_sides  d7 2-3  · mtd 3-10 · d30 5-12  · epoch 5-12    · lifetime 5-12

B64 read five windows as five duplicates. The uniqueness constraint it asked
for already exists in effect on (sport, surface, window_key) — adding one on
(sport, surface, window_label) would have been actively wrong, since
window_label is NULL for all of them.

**CLAIM 3 — "prop_coverage n=3,871 under NHL is MLB numbers" → NOT
CONTAMINATION.** `_pick_prop_tier` tags `'sport': sport` correctly per source
table, and `nhl_pipeline_props` genuinely contains the volume:

    nhl_pipeline_props   26,199 rows · 21,815 graded · 21,815 graded COVERAGE
    mlb_pipeline_props   36,894 rows · 36,894 graded ·  3,000 graded COVERAGE
    nfl_pipeline_props    2,931 rows ·  2,647 graded ·      0 graded COVERAGE

After the -300..+150 odds band and preseason exclusion, 4,247 NHL rows
survive. The reason the NHL figure sits within 3 of the cross-sport `ALL` row
(NHL d7 1277-1099 vs ALL d7 1280-1104) is simply that **NHL is almost the
entire COVERAGE pool** — 21,815 of ~24,800 graded COVERAGE rows. The
resemblance that looked like leakage is the arithmetic working.

### THE REAL RESIDUAL ISSUE, which B64 did not name

NHL `prop_coverage` publishes a **2284-1963 (n=4,247)** record while
**`public_receipts` holds ZERO NHL prop rows**. So it is a record over
PIPELINE rows that were never published to anyone — not a record of picks a
user could have followed. Every NHL prop is tier=COVERAGE and COVERAGE is the
tier the engine declines to publish, so the whole surface is by construction
unpublished.

That is the prop-record-inflation family, not a contamination bug — see
`project_prop_record_inflation_held_1003` (+274u displayed, held by choice) and
`feedback_surface_records_trust_levels` ("prop_prime is inflated — cite
sides"). Whether an unpublished-pipeline rollup should surface as a "record"
is a PRODUCT decision Andy has already taken once, deliberately. It should not
be quietly changed as a bug fix.

**DECISION NEEDED (Andy):** should `prop_coverage` surface for NHL at all,
given 0 of its 4,247 graded rows were ever published? Options: hide the
surface for sports with no published props; relabel it so it reads as model
coverage rather than a betting record; or leave it, consistent with the 10-03
decision.

VERIFY: compare `surface_records?surface=eq.prop_coverage` against
`public_receipts?sport=eq.NHL&market=eq.prop` (0 rows) and the
`nhl_pipeline_props` graded-COVERAGE count (21,815).

**Still open from B64:** nothing actionable as written. Closing claims 1-3;
the residual is a product question above, not a defect.

## 2026-10-09 · B70 — NCAAF full-slate spot check through our own lens

Andy 2026-10-09: *"i want to breakddown and spot check all college football
games thorugh our lens looking at raw data veirfying accuacry while also
assessing SOR/SOS and if we concur with engine."*

Three distinct jobs in one ask, and they want separating because two are
mechanical and one is judgement:

**(a) RAW-DATA ACCURACY — mechanical, scriptable.** Per game, confirm the
inputs the read is built on actually exist and are right:
  - `close_spread` present AND the sign convention correct (NCAAF: NEGATIVE =
    home favourite; this has been wrong before — `project_close_spread_sign_bug_914`)
  - SP+ off/def/overall present for both teams (currently 100% on the slate)
  - `home_ap_rank`/`away_ap_rank` — fixed 2026-10-09 in `3bd19b06`, now 29/70
  - team stats present for both teams in `team_stats_rolling`, and whether the
    row is current-season or a prior-season fallback (`*_stats_blend_label`)
  - whether the published line still matches the market (`project_published_lines_wrong_1007`
    — close_spread was being served as the OPEN)
  - pick/tier internal consistency: `primary_play.tier` vs any cap applied,
    conviction inside the tier's ceiling

**(b) SOR/SOS ASSESSMENT — mechanical.** Per game, the margin-rating gap
(`sor` home − away, now the opponent-adjusted points rating) against the market
spread. The rating is in points and the gap is meant to approximate a
neutral-field spread, so `sor_gap + HFA` vs `close_spread` is a like-for-like
comparison and the residual is the disagreement. Report it, do NOT treat it as
an edge: SOR/SOS is a sound descriptive rating but UNPROVEN as a predictor
(`project_sos_sor_root_cause_930`), and the SOR+defence combo test came back
inside noise.

**(c) DO WE CONCUR WITH THE ENGINE — judgement, not scriptable.** This is the
part Andy actually wants and it cannot be automated: reading each game's raw
inputs and saying whether the pick follows from them. The script's job is to
lay the evidence out per game so this can be done quickly and consistently;
the concur/dissent call is mine to make and state, with a reason.

### Scope and honesty constraints

- 70 upcoming NCAAF games, 46 of them on the 10-10 Saturday slate. At that
  volume the output must be a compact one-line-per-game table with a flagged
  subset, not 70 paragraphs.
- **The engine's NCAAF record is the context this runs in:** cards measured
  -29.2% ROI on 44 live-priced plays, conviction does not rank (r~+0.03), and
  the market itself is a coin flip at every slice on 6,329 games. So "do we
  concur" must not become a hunt for reasons to like picks. Where the raw data
  does not support the pick, say so; where it is genuinely ambiguous, say that
  too rather than manufacturing a verdict.
- Anything surfaced as a specific play must be DB-verified per CLAUDE.md
  rule 1 — no composed pick lists.
- Known live example to carry in: Maryland @ Ohio State (-34.5) shows
  `sweat_tier=PRIME` beside `primary_play.tier=LEAN`. That is CORRECT, not a
  defect — `sweat_tier` measures distance from the book, not pick confidence
  (relabelled 10-02). A spot check must not re-flag it.

### Timing

The useful window for the 10-10 slate is tonight. Picks are already locked
(`pick_locked_at` stamped 10-09T02:41Z), so this is a VERIFICATION pass and a
record of where we agree — not an opportunity to re-pick. If a genuine data
defect turns up, that is a different conversation and Andy's call.

FIX: build `ncaaf_slate_spotcheck.py` producing one row per game — market
line, SOR/SOS gap, SP+ gap, AP ranks, model projection, pick + tier, data-
completeness flags, and a residual column — then review the output and record
concur/dissent per game with reasons.

## 2026-10-09 · B71 — Red River close_spread sign is inverted (LIVE, user-facing)

Andy: *"Take a look at why Texas Okalhoma game ahs not data for colleg football
big game should have alot of data"*

The data is mostly there — 180 of 207 context columns are filled. What is wrong
is worse than missing: **the line direction is backwards on the biggest game of
the weekend.**

    ncaaf_20261010_Oklahoma_Texas
      close_spread      +6.5     -> by convention, OKLAHOMA favoured by 6.5
      open_spread       +6.5
      home_ap_rank      1        (Texas)
      away_ap_rank      None     (Oklahoma UNRANKED)
      home_sp_overall   22.4 (#7)   away_sp_overall 12.8 (#27)
      sp_gap            +9.6     -> Texas better by 9.6
      projected_spread  11.82    -> our model: Texas by 11.8
      primary_play      "Texas +6.5"

The convention was re-verified rigorously on 6,444 graded games, because
getting it backwards would invert several other conclusions:
    close_spread < 0 : home wins 78.9%  n=4,190
    close_spread > 0 : home wins 29.3%  n=2,240
    correlation(close_spread, home_margin) = -0.723
    spread_result agrees with (margin + spread > 0) on 6,428/6,444 = 99.8%
So NEGATIVE = home favourite, definitively. A stored +6.5 therefore says the
unranked road team is favoured by 6.5 over the AP #1 home team, while every
one of our own ratings has Texas better by 6 to 12 points.

TWO CONSEQUENCES, the second user-facing:
  1. A FABRICATED EDGE. The engine compared projected_spread (Texas by 11.8)
     against a line it read as Oklahoma by 6.5 and saw an ~18-point
     disagreement. That is the largest "edge" on the slate and it is an
     artefact of the sign.
  2. THE PICK LABEL MISREPRESENTS THE BET. It reads "Texas +6.5" — Texas
     TAKING points — when Texas is laying them. A user acting on that label
     bets the wrong number.

CONTEXT: market-vs-SP+ sign disagreement runs 18 of 254 comparable NCAAF games
(7.1%), and most are small honest differences (|sp_gap| 5-8). Red River is the
outlier by a wide margin: a 16-point swing with the #1 team as a home dog to an
unranked opponent. No line_history rows exist for this game, so the stored
value could not be reconciled against a book feed — which is itself part of the
problem.

ALSO FOUND while measuring:
  * TWO rows in ncaaf_game_results for 2026-10-10, home/away REVERSED
    (Oklahoma @ Texas and Texas @ Oklahoma, both unplayed). Same class as B65.
    Only the Oklahoma @ Texas slug has a context row and externals.
  * 'Virginia Tech Ho' and 'Virginia Tech' as separate rows for the same
    2026-09-12 game with DIFFERENT spreads (-14.0 and -18.5) — a truncated
    team name forking a game in two.
  * ncaaf_game_context.neutral_site is populated on 0 of 494 rows. Red River
    is at the Cotton Bowl. ncaaf_game_results.neutral_site IS populated
    (607 True) and compute_margin_strength reads it correctly, so SOR is fine;
    the gap is pre-game only.
  * Red River has 3 externals against 10-12 on comparable 10-10 games.

VERIFY: compare close_spread against sp_gap and ap ranks on
ncaaf_20261010_Oklahoma_Texas; re-derive the convention with the 6,444-game
test above before touching anything.
FIX: establish where close_spread for this game came from and whether the sign
flip is isolated or source-specific. Do NOT blanket-flip on sp_gap disagreement
— 7.1% of games disagree legitimately. Picks are locked
(pick_locked_at 2026-10-09T02:41), so a correction is Andy's call.

---

## 2026-10-09 · B72 — externals: NHL/NBA wired to 3 sources, NBA at zero

Andy: *"are you seeing any picks form them one xternal pull across sports, i
think we are droping the ball on numbe rof externals on some sports"*

pickdawgz IS working — 675 rows across MLB 574 / NCAAF 48 / NFL 39 / NHL 14.
Heavily MLB-skewed but not broken.

The real gap is the overall shape. 12,528 external_picks rows, 14 sources:

    sport    sources active   rows      puller defines
    MLB            13         9,293     18
    NCAAF           8         1,939     10
    NFL             8         1,101     10
    NHL             3           132      0  (delegates to externals_pro_core)
    UFC             1            63      3
    NBA             0             0      0  (delegates to externals_pro_core)
    NCAAB           0             0      9  (off-season, opens 2026-11-03)

pull_externals_nhl.py and pull_externals_nba.py are 44-46 line wrappers around
externals_pro_core, which carries exactly THREE sources — oddscrowd,
scoresandodds, pickdawgz. That matches NHL's 3 active sources exactly, so
nothing is failing; the coverage was simply never built out.

FIVE sources are MLB-ONLY: vsin, docsports, bettingpros, betfirm, tonyspicks
(1,526 MLB rows, nothing elsewhere). And five more — action, covers, dimers,
pickswise, sbr — are wired for MLB AND football but NOT for NHL/NBA, even
though their scrapers demonstrably work for two other sports already.

WHY THIS MATTERS NOW, not later: MLB has ONE upcoming game row and a
2026-11-05 season end, and MLB is the sport carrying 13 of our 14 sources and
74% of all external rows. NBA opens 2026-10-21 with THREE sources wired and
zero rows today. So the externals stack is built for the sport that is
finishing and nearly absent for the ones taking over.

FIX: wire action, covers, dimers, pickswise and sbr into externals_pro_core so
NHL/NBA get the same eight sources football already has. Their scrapers exist
and work; the gap is wiring, not scraping. Verify NCAAB's nine defined sources
actually fire when the season opens 11-03 rather than discovering it in
November.

## 2026-10-10 · B73 — externals: the per-source record is three different bets pooled

Andy: *"Are ther imrpivemtns that could be mde on extenrsls if so add to list"*

Today's work fixed the PLUMBING (dead subsets in three pullers, sbr not
landing for NCAAF, betfirm's expired 403, five MLB stubs counted as OK,
NHL/NBA 3→6 sources). What it exposed is that the way we JUDGE externals is
wrong, which matters more than coverage, because source weighting feeds the
engine.

### B73a · ml / rl / total are pooled into one hit rate — **the big one**

11,473 graded external rows, split by surface:

    ml      3439-2708   55.9%   n=6,147
    total   1388-1343   50.8%   n=2,731
    rl      1281-1314   49.4%   n=2,595

An **ml** pick is graded on WHO WON and has no fixed breakeven — it depends
entirely on the price. An **rl/total** pick does have one, 52.38% at -110.
Pooling them and comparing the blend to 52.38% is the documented
ml-vs-rl conflation (feedback_ml_vs_rl_conflation) applied to source records.

It distorts the leaderboard badly. Per-source, by surface:

    source           ml              rl            total
    dimers        77.1% n=468        —               —        <- ml ONLY
    sbr           59.6% n=428   40.0% n=5     52.7% n=328
    pickdawgz     59.4% n=451   43.5% n=62    51.8% n=137
    oddscrowd     56.2% n=1084  49.2% n=1148  50.7% n=1153
    action        55.0% n=1159       —               —
    covers        55.9% n=376   46.6% n=163         —
    scoresandodds 51.2% n=537   51.2% n=853   49.7% n=874
    pickswise     50.9% n=458   48.1% n=335   52.4% n=143
    docsports     49.5% n=759       —               —
    betfirm       45.6% n=180   36.8% n=19    57.6% n=66

dimers' headline 77.1% is an ml-only source picking win-probability
favourites. Favourites win straight up ~77% of the time at the prices dimers
selects; it is not a 25-point edge. Any weighting that reads the pooled number
ranks dimers top and is ranking "picks favourites".

FIX: record and weight per (source, surface). Never compare an ml hit rate to
52.38%.

### B73b · **externals are BELOW breakeven on spreads, pooled across every source**

rl is 49.4% on n=2,595 — and 2SE is ±2.0pp, so that is not noise. Six of the
seven sources with any rl volume sit under 50%. If the engine gives external
agreement any weight on a SPREAD pick, it is weighting a measurably
sub-breakeven input. Worth checking what signal_sources actually does with
external agreement on rl before adding any more sources.

### B73c · only 46.7% of graded rows carry a PRICE

5,354 of 11,473. Without `odds_american` an ml pick cannot be converted to ROI
at all, so the one surface where externals look strongest (ml 55.9%) is the
one we cannot value. A 55.9% ml hit rate is profitable or ruinous depending
entirely on whether those are -130 favourites or -250 ones.

FIX: capture the price on emit wherever the source shows one; report ml
performance as ROI, not hit rate, and refuse to report it at all where the
price is missing.

### B73d · grading coverage is uneven, and UFC is zero

    MLB    8893/9303  95.6%
    NFL     876/1112  78.8%
    NCAAF  1593/2094  76.1%
    NHL     111/263   42.2%
    UFC       0/63     0.0%   <- B67, foreign game_id space

NHL at 42% means more than half its external rows can never inform a source
record. UFC at 0% means its one live source (bfo) has never been judged.

### B73e · unverified-until-season, and a stub named `fetcher_todo`

NBA (opens 10-21) and NCAAB (opens 11-03) are now wired to 6 sources each and
BOTH show zero rows, so neither is provable today. The refresh subsets were
widened on evidence from other sports, not measured on these. Run
`--refresh --dry-run` for each once games exist. UFC has one real source plus
a stub literally named `fetcher_todo`.

### B73f · the five MLB stubs: implement or delete

cbs, oddsshark, scp, fangraphs, ballparkpal each have a `return [], 200` body
and are now out of dispatch (259e20cb) so they no longer report a false OK.
Their SOURCE_REGISTRY config remains with the intended url. Decide per source
whether to write the parser or drop the config — leaving a defined-but-absent
source is what made MLB look like it had 18.

### NOT a bug, recorded so it is not re-litigated

NCAAF per-game source coverage (median 6, max 7) is near its structural
ceiling. The pick sites do not write up a 60-game Saturday: dimers covered 8
games, pickdawgz 9, pickswise 12 of 46. Only the money-flow and consensus
sources (scoresandodds, oddscrowd, covers, sbr, action) scale to a full slate.
Reaching 7-10 per NCAAF game needs NEW full-slate sources, not better wiring
of the current ones.

## 2026-10-10 · B74 — Jerry's by-game analysis has a record and nobody can see it

Andy: *"add to list adding to jerry recipet tab Jerry by game analysis record,
anything that published in game detail across every sport"*

The record EXISTS and is mostly graded. It is simply never rolled up, so there
is nothing for the receipts tab to read.

### What exists today

`public_receipts.surface = 'game_read'` is Jerry's per-game analysis — the read
that publishes in game detail. 1,510 rows, 1,215 graded (80.5%):

    sport    rows   graded      W-L     hit%
    MLB       773      741   432-309   58.3%
    NCAAF     454      327   174-153   53.2%
    NHL       163       91    51-40    56.0%
    NFL       120       56    36-20    64.3%
    NBA         0        0      —        —     <- nothing published at all
    NCAAB       0        0      —        —     off-season, opens 11-03

Pooled that is 693-522 = 57.0% on n=1,215, which is a real record on a real
sample and is currently invisible.

### Why it is invisible

`surface_records` holds 293 rows across 34 distinct surfaces — sharp_card,
prop_jerry, potd, dawg, ledger, ladder, mlb_sides, nfl_sides, nhl_sides,
ncaaf_sides, ufc_sides, panel_consensus, prop_prime/strong/lean/coverage,
model_* and voter_* — and **not one `game_read` row**. So:
  * nothing aggregates it per sport or per window
  * the app has no row to query (AdaptiveRecordChips / HomeStreakBanner and
    the receipts surfaces all read `surface_records`)
  * Jerry's most-published artefact is the one surface with no scoreboard

### B74a · ~~roll game_read into surface_records~~ **DONE** `828d3df3`

Add it to whatever writes surface_records (compute_surface_records) with the
same window_key treatment the other surfaces get. Note the existing
`*_sides` surfaces are the ENGINE's side picks, not Jerry's read — do not
fold game_read into those, they are different claims and conflating them
would repeat the sweat_card-vs-sharp confusion.

### B74b · ~~close the grading gaps before publishing the number~~ **DONE** `828d3df3` — and the figures below were WRONG

**CORRECTION 2026-10-10.** Those four percentages were bad measurements. The
denominators included FUTURE games, in-progress games, VOIDs and preseason.
Of receipts that could actually be graded:
    MLB    755/758   99.6%
    NCAAF  329/329  100.0%
    NHL     91/91   100.0%
    NFL     56/58    96.6%
There was no broad grading gap. What was real: 26 NFL game_read picks were
ungraded, because receipts carry the Odds-API hash while nfl_game_results
keys '20260927_MIN_TB' and all 26 have matchup=NULL, so neither the game_id
join nor the matchup fallback could resolve them.

FIXED by adding a `<sport>_game_context` bridge as a third resolution path in
grade_public_receipts (context holds the same hash as the receipt plus the
team names). 26/26 resolved; bridges built for MLB 41, NCAAF 467, NFL 67,
NHL 134. Of the 26: **2 graded** (ATL @ GB 9/25 hand-checked), **24 VOIDed**
as season_type='PRE' with zero August rows in nfl_game_results to grade
against — preseason never enters a published record
([[project_nhl_preseason_record_930]]). A re-run across ALL surfaces leaves
only 'no final score' rows, i.e. nothing further is gradeable.

I also initially called those 24 "pickless shells" — also wrong. Every one
carries market, pick_side, conviction and a jerry_reads source_id; 17 carry a
pick_line. They were real picks, just unlabelled.

### B74c · ~~NBA publishes no game_read at all~~ **BUILT** `9b3a33f7`

Zero rows, and the season opens 2026-10-21. Either Jerry's read does not run
for NBA or it runs and never writes a receipt. Worth settling before opening
night rather than discovering an empty NBA tab in two weeks — same shape as
the NBA externals gap (B72).

### B74d · ~~audit everything game detail publishes~~ **DONE** `828d3df3`

game_read is the main one, but game detail also surfaces prop rows, external
picks and (as of 03be17d2) Jerry's prop read. Enumerate every artefact the
game-detail screen publishes, and for each say whether it is graded and
whether it is rolled up. The pattern today is that grading exists and rollup
does not, so the audit is cheap and the answer is probably "several".

**Client build required for the tab itself** — currently held pending the
Split rework/rename. B74a/b/c are all backend and can land before it.

### B74 RESULTS (2026-10-10)

**B74a done.** 25 game_read rows, 5 sports x 5 windows. Lifetime ALL
668-506-16 = **56.9% on n=1,190**; MLB 58.3%, NFL 62.1%, NCAAF 53.2%,
NHL 54.2%. Reads public_receipts (the frozen published claim) not jerry_reads;
NOT folded into `<sport>_sides` (those are the ENGINE's picks, not Jerry's);
preseason excluded via the shared `_is_preseason` gate — which mattered, NHL
came out n=48 rather than 91 because 43 were preseason.

**B74c cause found.** `jerry_reads` has ZERO NBA rows (MLB 812, NCAAF 499,
NHL 163, NFL 127, UFC 88, NBA 0) because **`generate_nba_game_reads.py` does
not exist**. Generators exist for MLB/NCAAB/NCAAF/NFL/NHL/UFC with NO shared
core — each is standalone, 320-2,908 lines — so this is a real build, not
wiring. `generate_ncaab_game_reads.py` (359 lines, the other basketball
sport) is the template. NBA opens 10-21 and nba_game_context already has 81
rows, so it is testable now.

**B74d found TWO MORE unrolled surfaces**, same shape game_read was:

    surface        rows   graded   rolled up?
    prop_jerry    15331     49%    yes
    game_read      1510     81%    yes (B74a)
    sweat_card     1056     94%    NO   <- 988 graded picks, invisible
    sharp_card      630     94%    yes
    ledger          255     91%    yes
    daily_degen     158     91%    NO   <- 144 graded picks, invisible
    potd            149     93%    yes
    dawg            131     91%    yes
    external_picks 12835     89%    yes

**RESOLVED 2026-10-10, Andy concurred.**

* `sweat_card` **ROLLED UP** (`114a66c6`) — 20 rows. Lifetime ALL
  584-404-18 = **59.1%, +97.68u** on n=1,006; MLB 562-373-17 = 60.1%,
  +109.84u. ⚠ Flagged before it surfaces: **NFL is 4-12 (25.0%) on n=16** and
  **NCAAF 18-19 (48.6%) on n=38** — thin and negative, and visible the moment
  anything renders per-sport. NOT merged with `sharp_card` (dashboard top-8 vs
  Steam Room slate — different denominators). Units are a MIXED BASIS: only
  35% of picks carry `pick_odds`, so real price where present and -110
  otherwise. Hit rate is exact; units improve as price coverage does. Dropping
  the unpriced 65% was rejected — it would shrink a published record from 988
  picks to 350.
* `daily_degen` **NOT rolled up, by decision.** It is a **4-leg parlay**
  surface: 25-119 = 17.4% on n=144, **zero prices on all 144**, nothing since
  2026-09-19. A 4-leg parlay at ~52% legs hits ~7.3% naturally, so 17.4% is
  beating that by a wide margin and may be strongly profitable — but with no
  prices the honest number (ROI) is uncomputable, and publishing "17.4%"
  beside surfaces at 55-60% would misrepresent it badly. **Next step: capture
  leg prices, then decide.** Also settle whether it is retired.

### B74c RESULT (2026-10-10) — built and verified

`generate_nba_game_reads.py` created and wired into `nba_pipeline.yml` right
after `nba_game_context --days 4`. This is the **rebuild already scheduled**,
not a revival: `mlb_pipeline.yml:1739` records the 8/17 NBA-from-scratch reset
that deleted the legacy generator with BDL, noting "Rebuild scheduled for
pre-season (Oct 22)". NBA opens 10-21.

**The sign convention is the OPPOSITE of NCAAB's and was verified, not
copied.** `corr(projected_home_wp, projected_spread) = -0.996` (n=18) ⇒
`projected_spread` is NEGATIVE when HOME is favoured, same as `close_spread`.
So `home_edge = close_spread - projected_spread` (a subtraction; NCAAB adds).
Copying NCAAB would have named the **wrong favourite on every game** — the
class of [[project_close_spread_sign_bug_914]] and the Red River inversion.
Both conventions are written into the struct so the LLM is told, not left to
infer.

Verified end to end: dry run over 6 games (GSW @ LA edge +5.5 → "Model likes
Los Angeles Lakers", Lakers home ✓); live run wrote a `jerry_cache` row with
`sport='NBA'` uppercase and an 846-char narrative with correct direction; and
**`jerry_reads` has its first NBA row ever**, stamped `game_date` from the
context row rather than the run date ([[project_ncaaf_grading_gap_908]]).
Market lines are on 18/81 rows (odds window < schedule horizon), so
`struct.meta.absent_blocks` names what is missing per game and the read cannot
invent a Monte Carlo or money-flow block.

**B74 is now fully closed: a, b, c, d.**

---

## B75 — THE TIER LADDER SORTS BY CHALK, NOT BY EDGE (2026-10-11)

ANDY: "we need a tier sytem that works, that if we say this is Prime Jax -6.5
anytime we do that it's hitting more than not." He is right, and the reason it
does not hold today is now measured rather than suspected.

**Aggregate hit rate was the wrong unit all along.** Nobody plays the whole
board. The TIER is the promise, so the tier is what has to be measured — on
its own, repeatably. Measured on `public_receipts` (19,258 rows), props,
against the price actually paid, POST-09-22 (outside the L5 leak window):

```
tier       hit%    implied%   edge    median odds   n
PRIME      59.3%     60.9%    -1.6       -150      226
STRONG     55.6%     53.8%    +1.7                 396
LEAN       53.1%     54.2%    -1.1                 559
COVERAGE   56.7%     49.6%    +7.0                 127
```

**PRIME is the chalkiest bucket and loses to its own price.** It hits a lot
because it is stamped on -150 favourites, not because it finds mispricing.
The "71%" that has been displayed is 72.5% pre-09-22 (the L5 leak window,
[[project_prop_l5_leak_922]]) collapsing to 59.3% clean — and even that 59.3%
is BELOW the 60.9% the market implies.

**COVERAGE — the bucket we assign 0.0 units and refuse to publish — is the
only one with a positive edge**, +7.0pp post-leak (n=127) and +4.9pp pre-leak
(n=280). Two independent windows, same sign. Those are props priced near a
coin flip, which is exactly where being right pays.

Sides are the same shape, with an unstable ladder on top: post-09-22 PRIME
60.2% (n=108) vs STRONG 46.9% (n=113), but PRE-09-22 it INVERTS (PRIME 56.3%
n=343 vs STRONG 60.0% n=255). A ladder that flips sign across a date boundary
is not a ladder.

### B75a — retier on EDGE, not on probability
Tier from `model_probability - implied_probability`, not from conviction.
Inputs already stored per row (`projection`, `prop_line`, `se`, book odds), so
this is a scoring change, not a data project. Then PRIME means "we think the
market is wrong by >= X", a promise that can hold, instead of "this is a
favourite", which the user can read off the board for free.
MUST ship as a shadow column first and be graded against the live tier before
it replaces it ([[feedback_suppression_gate_needs_shadow]]).

### B75b — stop publishing an inflated PRIME number
The displayed prop record is leak-contaminated and was HELD by choice
([[project_prop_record_inflation_held_1003]]). Post-leak PRIME is 59.3%, and
it does not beat its price. Decide: restate, or floor the window at 09-22.

### B75c — the COVERAGE bucket is being thrown away
+7.0pp vs price on n=127. Before acting, control for WHY: COVERAGE is assigned
when the ensemble has no credible chip, so this may be "the model shuts up
exactly when the market is uncertain, and uncertainty is where the edge is."
If that survives a control, COVERAGE is a publishable tier, not a bin.

### B75d — tier must be measured PER SPORT PER MARKET
NCAAF sides PRIME 37.0% (n=27) sits BELOW STRONG 52.9% (n=70). NFL sides
STRONG 26.9% (n=26). `sweat_card` sides are FLAT across the whole ladder
(PRIME 59.2% / STRONG 59.1% / LEAN 58.2%) — the label adds nothing there.
One global ladder cannot be right for seven sports.

## B76 — ANYTIME TD (OVERS ONLY) + FIRST TD PROBABILITIES (2026-10-11)

ANDY, on Sam's Models going 100 -> 8.1k followers in a week: "For AT TDs dont
wnat to assess udner just overs and first td probabliltiees, where to surface
that firts td maybe in game details card?"

**OVERS ONLY — no anytime-TD unders, by directive.** An anytime-TD under is a
lay on a human being not scoring; it prices badly, grades meanly and is off
brand.

Feasibility checked, not assumed:
* `nfl_player_stats` — 4,512 player-weeks for 2026 (wks 1-5), 262 with >=1 TD.
  `target_share` and `wopr` populated on 4,512/4,512. Plus carries, targets,
  air-yards share, rushing/receiving first downs.
* `nfl_snap_counts` (offense_pct) and `nfl_injuries` present.
* Implied team totals derive from `close_spread` + `close_total`, both on
  `nfl_game_context`.
* **`anytime_td_over` has 2 rows in `nfl_pipeline_props`.** Four including
  unders. We do not pull this market at all — that is the first gap.
* **No play-by-play table exists** (`nfl_play_by_play` / `nfl_pbp` both 404),
  so NO inside-10 / goal-line / yardline splits. Sam's INSIDE 10 / 11-20 /
  OPEN FIELD radar is NOT reproducible from what we hold.

### B76a — pull the anytime-TD market
Odds-pull change. Without a line there is no edge to compute and nothing to
grade against. Blocks everything below.

### B76b — anytime-TD model, overs only
P(scores) from share-of-opportunity x implied team TDs. Gradeable from
`nfl_player_stats` (rushing_tds + receiving_tds) from day one.
Tier it on EDGE per B75a from the start — do NOT inherit the chalk ladder, or
the most likely scorer becomes PRIME and we repeat the whole defect in a new
market.

### B76c — first-TD probabilities, surfaced in the game detail card
Andy's placement. FTD is roughly P(anytime) reweighted by drive order and
first-possession share. Honest limit: without play-by-play this is an
approximation of an approximation, and it must be labelled as a CHANCE, not a
pick — the one thing Sam does that we can adopt wholesale is "CHANCES, NOT
PRICES" on a surface that is informational.

### B76d — play-by-play source
Gates real FTD (and red-zone work everywhere else). nflverse PBP is free.
Until then B76c ships caveated or not at all.

## B77 — SURFACE THE MARGIN DECOMPOSITION (2026-10-11)

Sam's "WHERE THE MARGIN COMES FROM" — bars that SUM to the projected margin,
with a sentence saying so. **We already compute this and discard it**:
`signal_confluence_breakdown` and `_ensemble_sources[].contribution` carry
per-signal point contributions and prose. Today they render as unordered
chips. Mostly a presentation change over data we already hold.

## B78 — MODEL CONSENSUS ROW IS SHOWING THINGS THAT ARE NOT LENSES (2026-10-11)

From Andy's TB @ DAL screenshot. Measured on the NFL board:
```
tile    n     mean     sd     range
v3     316   +2.19    2.45   [-15.7, +15.1]
v4      58   +1.73    0.22   [ +0.8,  +2.0]   <-- CONSTANT
MC      94   +2.25    7.72   [-13.5, +21.6]   <-- 3x the scale
Panel   84   +0.58    2.89   [ -7.3,  +6.3]
```
* **v4 is a constant** (sd 0.22). "DAL 1.7" is what v4 says on every NFL game.
  It is not a lens; it manufactures a fourth agreeing voice. Drop it while
  sd ~ 0.2, self-healing like the SP+ dupe at GameDetailV2.tsx:2272.
* **MC is 3x wider than the others**, so MC 19.4 is not a per-game outlier.
* Three of the seven tiles have no margin at all and fake one to fit the row:
  LR is `(p-0.5)*10` (:2293), GOAT is `+/-1` sign-only (:2309), CONF is a
  signal count. A shared number-line axis would plot these as if they were
  point spreads — do NOT put them all on one.
* Totals: MC 12.75 / v3 11.47 / Panel 11.38 MAE vs the MARKET's 9.90 (n=65).
  All three lose to the close; MC is worst and is the one shown in green.
* Keep LR's "ml lens" / GOAT's "independent" neutral labels — they are the
  09-17 guard against the win-prob vs cover-prob category error. Fix the
  WORDING ("wins outright - not ATS"), not the logic.
* Keep the 47-58 neutral band on cohort pills. Fading a 48.3% trend returns
  51.7%, still under breakeven.

No OTA path, so all of B78 rides the build already held for the Split rework —
bundle it rather than planning a separate pass.

### B75e RESULT (2026-10-11) — history de-leaked; a real but SMALL tier signal exists

`backtest_prop_tier_clean.py`, read-only, 21,209 pre-09-22 MLB props
re-scored with every lookback recomputed from games STRICTLY BEFORE each
prop's own `game_date`. Turns a 1,183-row clean backtest into a 21,209-row
one without waiting for a season we no longer have.

**The leak, quantified.** Stored vs clean `player_l5_hit_count` agree on
only **7,788 / 13,593 = 57%**. It was wrong on nearly half the rows.

```
                 hit     implied    edge
leaked L5 = 5   95.8%     60.6%    +35.2     <- the tautology
clean  L5 = 5   71.0%     68.4%     +2.6     <- what it is actually worth
```

**The live tier's apparent edge is the leak.** On these same rows PRIME
shows +17.5pp over fair (n=2,128) — but PRIME was ASSIGNED using the
leaked L5, so that number measures the leak, not skill. The honest
ceiling of any leak-free feature we own is ~+4pp, not +17.5pp.

**A monotone leak-free signal does exist, and it survives a temporal
holdout** (fit 2026-04-23..08-27, test 08-28..09-21):

```
clean season    test hit   test mkt    edge        n
s<30%             18.5%      23.6%     -5.1      4,430
s30-45            39.1%      40.1%     -1.0      3,086
s45-55            49.3%      50.0%     -0.7      1,893
s55-70            62.1%      59.7%     +2.3      3,854
s70%+             76.0%      71.9%     +4.1      3,415
```

Same shape on clean L10 (0-2 -> -5.1, 7-8 -> +3.8, 9-10 -> +2.8). A ~9pp
spread that holds on held-out games. Tiering on it: **top 10% = +5.3pp
over fair, n=1,259, +/-2.8**, against -0.3pp for the population.

**What does NOT transfer: a fitted per-cell edge table.** With a properly
sized fit set, corr(fit edge, test edge) is +0.364 (season), **-0.235**
(L10) and +0.139 (season x L10). An earlier lopsided split (fit n=1,395)
showed +0.914 and was an artefact — the per-cell magnitudes are noise.
Tier on the FEATURE BUCKETS, never on a learned edge lookup.

**The honest ceiling.** +4 to +5pp is measured against the DE-VIGGED fair
price. We pay fair plus overround, which on these props runs ~2.5-5pp per
side. So the top tier is breakeven-to-modestly-profitable, not a 70%
winner. A PRIME that promises "hits more often than not" is supportable;
one that implies a large edge is not.

**The most reliable component we own is a refusal.** SKIP: 40.8% hit vs
44.9% implied, **-4.1pp on n=13,470** (+/-0.9). The engine identifies what
NOT to bet far better than it identifies winners. Keep SKIP untouched.

### B75a SPEC, now evidence-backed rather than guessed
1. Tier on leak-free `clean_season_hit_rate` + `clean_L10` buckets. Live
   rows already carry clean features — the `before_date` fix landed 09-22,
   so no recompute is needed going forward.
2. Set the top tier's published expectation at **+3-5pp over fair**.
3. Keep SKIP exactly as-is.
4. Shadow column first, graded against the live tier before it replaces
   anything ([[feedback_suppression_gate_needs_shadow]]).

**CAVEAT, load-bearing.** This is MLB props only, and MLB's season is
over. The ladder is validated on the one sport we can no longer bet. The
SHAPE should transfer to NFL/NHL; the THRESHOLDS should not be assumed to.
Re-validate per sport as sample builds — [[B75d]] already says one global
ladder cannot be right for seven sports, and this is the evidence for it.

## B79 — NFL/NCAAF PENALTY FEATURES ARE LAST SEASON'S, AND SIX SIGNALS READ THEM (2026-10-12)

The `context_features_preseason` watchdog has been firing CRITICAL and was
being read as cosmetic, because its own docstring said "nothing currently
reads the penalty columns, so that specific number is inert". That sentence
was wrong. Six ENABLED signals read them directly:

```
nfl_penalty_away_undisciplined    ctx.away_penalties_pg >= 7.5  -> HOME_RL
nfl_penalty_home_undisciplined    ctx.home_penalties_pg >= 7.5  -> AWAY_RL
nfl_penalty_sloppy_both           avg of both >= 7.0            -> OVER
ncaaf_penalty_away_undisciplined  ctx.away_penalties_pg >= 8.0  -> HOME_RL
ncaaf_penalty_home_undisciplined  ctx.home_penalties_pg >= 8.0  -> AWAY_RL
ncaaf_penalty_sloppy_both         avg of both >= 7.5            -> OVER
```

They are not dormant: 22 firings on NFL and 26 on NCAAF in the audited
window, and `ncaaf_penalty_away_undisciplined` is one of the worst signals
measured (36.0% against a 54.1% blind, -18.1pp).

**MEASURED, 388 upcoming NFL team-slots on 2026-10-12:**

```
stored == LAST SEASON's rate exactly   364  (94%)   <- prior-season fallback
matches a current-season rate           12  ( 3%)
neither                                 12  ( 3%)

TB   stored 5.59  = 2025 (95/17)    2026 actual 8.00   row updated 2026-10-08
GB   stored 6.18  = 2025 (105/17)   2026 actual 9.20   row updated 2026-10-07
BAL  stored 5.82  = 2025 (99/17)    2026 actual 9.75   row updated 2026-07-28
```

**THE BLEND IS NOT THE BUG.** `_nfl_blend_pg` called today returns 8.00 for
TB, 9.20 for GB — correct. `NFL_BLEND_UNTIL_GAMES = 3` and every team has 5
games, so the current season should win outright. The stored rows simply
carry the fallback written when the current season had no games, and
nothing has rewritten them.

**updated_at IS NOT A FRESHNESS SIGNAL.** Rows stamped 2026-10-07 and
2026-10-08 still hold 2025 values, so some updater bumps the timestamp
without refreshing team stats. Any check that trusts updated_at to decide
whether features are current is checking the wrong thing — including the
watchdog's own heuristic.

**EFFECT ON PICKS.** 2025 penalty rates run ~2-3/game BELOW 2026's while the
thresholds sit at 7.5 / 8.0, so on the upcoming slate 19 team-slots flip:
17 where the signal should fire and stays silent, 2 where it fires on a
four-month-old number (NYG@HOU away stored 7.53 vs an actual 7.20).

### B79a — re-enrich the upcoming slate
`nfl_game_context.py` takes no date-window argument; the full enrichment
covers roughly a two-week forward window and everything past 2026-10-22 is
still stamped 2026-07-28/29. Needs a window flag and a backfill pass.

### B79b — a freshness assertion that does not trust updated_at
Nothing asserts a game's features were computed after the season started
before picks are generated from them. The test must compare the STORED
VALUE against a recomputable ground truth (season total / games played),
which is exactly what the watchdog does and what every consumer should.

### B79c — MEASURED: it is EVERY blended column, not just penalties
`_blend_off` writes ~20 columns the same way, and penalties were only
noticed because a watchdog happened to cover them. Checked the same
precise way on the same 388 upcoming team-slots:

```
column            stored == last season's rate exactly
penalties                 364 (94%)
pass_yards                364 (94%)
rush_yards                364 (94%)
pass_tds                  364 (94%)
penalty_yards             364 (94%)
```

So the finding is not "six penalty signals read a stale column", it is
"the upcoming slate's TEAM STATS are last season's", and those feed the
ratings and projections as well as the signals. This is the real size of
B79 and it should be fixed at the enrichment, once, rather than per
consumer.


### B80 · 7 FBS teams have NO row in either NCAAF stats table — OPEN

Found 2026-10-19 while restricting the points/G ranking universe. These teams
appear in `ncaaf_game_results` with 3+ completed games but have ZERO rows in
`ncaaf_team_stats` (267 teams) AND zero in `ncaaf_team_defense_stats` (228):

    Appalachian State · Connecticut · FIU · Hawaii  (+3 more)

Checked for the STL-name-mismatch shape first — it is NOT a spelling variant;
the only near-match in the defence table is a different school (Central
Connecticut). The CFBD pull simply does not cover them for 2026.

CONSEQUENCE: their Team Stats card is blank, and they are excluded from every
NCAAF percentile pool, so the ranks the other 133 teams carry are computed over
a universe that is missing real FBS sides.

NOT introduced by the points/G work — pre-existing, and that script correctly
drops them rather than ranking them in a 140-team pool while every other row on
the same card ranks over 133.

FIX: find why the CFBD team-stats pull misses them (conference filter? a
`division` predicate?) before trusting any NCAAF percentile.
VERIFY: compare the team set in `ncaaf_game_results` (3+ games) against
`ncaaf_team_stats` for the season.


### B81 - UFC: two feeds number a card differently - **GRADER FIXED** `f33b9ca5`, source still OPEN

Measured 2026-10-19 over 95 joinable UFC reads: `fight_order` disagrees between
the picks/reads feed and `ufc_fight_results` on **16**. Root cause is two
independent enumerations of the same card - `ufc_resolve_fights.py:196` and
`ufc_score_card.py:205` both assign `fight_order: i + 1` from their own scrape
order, while the results feed numbers by the results page.

FIXED in the grader: `grade_ufc_jerry_reads.py` now resolves a fight by the
read's own snapshot fighter NAMES and refuses rather than falling back to the
index. That corrected one grade (ufc_2026-10-10_8, Julius Walker: Loss -> Win;
record 12-7 -> 13-6).

STILL OPEN: the numbering itself. Anything that joins these two tables on
`fight_order` is wrong 17% of the time. Audited today, nothing else does -
`ufc_grader.py` matches ESPN by name, and its `order: fight_order.asc` is only
an iteration sort - but a future consumer will reach for the index because the
game_id embeds it.
FIX: make one of the two feeds adopt the other's ordering, or stop embedding
`fight_order` in the UFC game_id so there is no index to be tempted by.

### B82 - UFC reads carry NO price, so the record cannot become ROI - **CLOSED** `7594d829`

All **103** `jerry_reads` rows for UFC have `price_american` NULL, across every
event from 08-01 to the upcoming 10-24. `ufc_picks` DOES carry odds
(`odds_a_median`, `odds_b_median`, `odds_a_best`, `odds_b_best`,
`odds_book_count`) - they are simply never joined through to the read.

WHY IT MATTERS MORE HERE THAN ANYWHERE: UFC is a moneyline market, so breakeven
moves with the price. The read record is 13-6 (68.4%) and that number cannot be
converted into profit or loss. For comparison, the model's own PRIME tier hits
63.2% at an average decimal price of 1.43 - 69.7% implied - which is a **-13.5%
ROI**. A hit rate without a price is not a record.

FIX: join `ufc_picks.odds_{a,b}_median` onto the read at synthesis time, same
as `price_picks.py` does elsewhere.

### B83 - the UFC engine has stopped making picks - **ANSWERED, premise wrong** `c1875cec`

Conviction and pick rate both collapsed in late August and have not recovered:

    date        reads  picks  conviction min/med/max
    2026-08-01     3      3        68 /  72 /  78
    2026-08-15    10      6         8 /  60 /  79
    2026-08-29     6      0         0 /  28 /  28
    2026-10-03    13      0        18 /  22 /  38
    2026-10-17     7      1        12 /  22 /  38
    2026-10-24     8      0         8 /  20 /  28

Every PASS is `call_market='pass'`, so this is the engine declining, not a
grading gap - and the grader is confirmed correct. 10-24 is an 8-fight card
with zero picks. Read coverage is also thin: 7 reads for a 13-fight card on
10-17, 13 for a 14-fight card on 10-03.

ALSO: `public_receipts` holds **0** UFC rows, so UFC appears in no
receipt-based record anywhere (this is B73d's UFC half).

OPEN QUESTION for Andy: is near-total abstention the intended posture given the
model loses at price in every tier, or did a gate move in late August? Either
way the app should say which, because a tab that publishes nothing reads as
broken.

### B84 - UFC FADE reads were graded on the MODEL's pick - **CLOSED** `7594d829`

`upsert_read` encodes the verdict in `call_side` (BACK = model's side, FADE =
the OPPOSITE), but `grade_one` resolved the picked fighter as
`snap['model_pick_fighter'] or <fighter at call_side>` - and
`model_pick_fighter` is always the fighter at `recommended_side`. So every FADE
was scored on the model's fighter while the app had published the other corner.

4 of 19 graded reads are FADEs; ALL FOUR were stored backwards. All 15 BACKs
were correct, which is why it hid - and the error FLATTERED us, because a fade
opposes a favourite and the favourite usually wins.

    13-6 (68.4%)  ->  11-8 (57.9%)  CI [36.3, 76.9]
    priced: avg implied 61.6% · -3.14u on 19 · ROI -16.5%

ALSO RETRACTS the 12-7 -> 13-6 correction in `f33b9ca5`, which used the
grader's own by-name path and inherited this defect. Never validate a grader
with the grader.

### B85 - one UFC duplicate read is still double-counting a loss - **CLOSED 2026-10-19**

`ufc_2026-10-10_8` (generated 10-04, when Meerschaert vs Walker was fight 8) and
`ufc_2026-10-10_7` (generated 10-08, after the card was renumbered) are the SAME
fight. Both graded Loss, both priced +250, so one fight books a full unit of
loss twice.

Recurrence is now PREVENTED (`_existing_gid_for_fight` in `7594d829` reuses an
existing read's game_id when the snapshot names the same pair), but the existing
row is untouched.

DONE, on Andy's go-ahead. `ufc_2026-10-10_8` (id 7490) is now `result = Void`
with the reason written into `audit_notes`, and the NEWER row
(`ufc_2026-10-10_7`, generated 10-08) is retained.

Guarded before writing: same fighter pair, same `call_side`, and the kept row
is the newer of the two - all three asserted, so a non-duplicate could not have
been voided by accident.

AND THE GRADER NOW RESPECTS A VOID. The fight still has a winner, so
`grade_one` would happily settle it again and the next `--regrade` would have
quietly restored the loss. `grade_ufc_jerry_reads.py` now skips any row whose
result is already Void and prints that it did; clearing `result` back to NULL is
how a void gets re-opened. Verified: a full `--regrade --dry-run` over 91 dates
now reports "left VOID (not re-graded)" and changes nothing.

    11-8 (57.9%) · -3.14u on 19 · ROI -16.5%
    ->  11-7 (61.1%) · -2.14u on 18 · ROI -11.9%   CI [38.6, 79.7]

Still a losing book - the void removed a double-counted loss, it did not make
the surface profitable.


### B86 - NBA game cards rendered EMPTY on opening night - **CLOSED** `3cd5295f`, needs the build

ANDY, 2026-10-20: "NBA game card are emty with no precitions or write up, also
... we need nba surfacing to usrer why isnt it?"

THE DATA WAS NEVER THE PROBLEM. All three opening-night games carried
projected_spread, projected_total, projected_home_wp, 10,000-sim
mc_probabilities and Elo, and their jerry_reads carried 276-320 char
short_reads, 598-830 char long_reads AND prices.

Two id-namespace misses, both the same shape as bugs already documented in
these files for NHL on 09-28:

  * the ctx prop chain in app/index.tsx ran MLB -> NFL -> NCAAF -> NHL ->
    `null` with NO NBA branch, so ctx was null and EVERY ctx-driven section
    rendered empty
  * `_jerryFor` tried `reads[game.id]` and an NHL-specific key only, while
    jerry_reads(NBA).game_id is an ESPN id (401909088) and selectedGame.id is
    an Odds-API hash - zero overlap, so no write-up ever resolved
  * GameDetailV2's own ctx-table fallback also omitted NBA

THE PATTERN WORTH REMEMBERING: a per-sport chain ending in `: null` fails
silently and looks exactly like missing data. This is the third sport to pay
for it. NCAAB opens 2026-11-03 with no map at all - it will be the fourth
unless its branch lands with the season.

### B87 - the NBA "in review" banner must be REMOVED when the build ships - **CLOSED** `112fd145`

`sport_registry.NBA.state_message` was set 2026-10-20 so users could see WHY
the NBA cards were vacant:

    "Opening night is live. Per-game predictions and write-ups are not
     displaying on NBA game cards yet - that fix is built and in review for
     the next app update. The models are running normally behind it, so any
     NBA play that clears the tier gate still posts to the Sweat Card and The
     Sharp in the meantime."

It is TRUE today and becomes a LIE the moment the build carrying `3cd5295f`
reaches users. Nothing expires it automatically.

FIX: clear `state_message` for NBA (`UPDATE sport_registry SET state_message
= NULL WHERE sport = 'NBA'`) as part of shipping the build, and confirm an NBA
game card renders a predicted score and a write-up before clearing it.
Backend-only either way - no release needed to change or remove the copy.

Also corrected while there: NBA `return_date` read 2026-10-21, the day AFTER
the opener, against `season_start` 2026-10-20.


### B88 - MODEL RECORD ACROSS SPORTS: spreads and totals lose, moneylines hold - **MEASURED 2026-10-20**

ANDY: "add to list audit of model record across sports". Built
`audit_model_record.py` and ran it over 1,453 graded `game_read` receipts.

THE POOLED NUMBER HID THE WHOLE STORY. surface_records publishes
`ALL 792-602-17, 56.8%`, but a moneyline, a spread and a total do NOT share a
breakeven - flat 52.38% on a spread/total, price-dependent on a moneyline - so
pooling them produces a rate comparable to nothing. Split:

    sport  market      W-L-P      hit%     ROI@price   breakeven  verdict
    MLB    ml        201-153-0    56.8%      +19.7%      56.7%    inconclusive
    MLB    total     230-144-13   61.5%      -17.4%      52.4%    LOSES
    NCAAF  ml         71-40-0     64.0%       +6.9%      59.8%    inconclusive
    NCAAF  spread    136-142-3    48.9%      -11.4%      52.4%    LOSES
    NCAAF  total      14-17-0     45.2%      -15.8%      52.4%    LOSES
    NFL    ml         26-12-0     68.4%      +10.7%      58.3%    inconclusive
    NFL    spread     18-23-0     43.9%      -16.2%      52.4%    LOSES
    NHL    ml         90-68-0     57.0%       -2.7%      57.2%    inconclusive
    UFC    fight      11-7-0      61.1%       -5.2%      45.2%    n<30

THE PATTERN IS CONSISTENT ACROSS EVERY SPORT WITH VOLUME: the moneyline read
is positive or break-even; the spread and total reads lose at the price. That
generalises project_nfl_curated_spread_is_the_leak_1018 (NFL spread 27.1% ROI
-45.9% while its ML read was +10.7%) from one sport to four.

THE SHARPEST SINGLE LINE IS MLB TOTALS: a 61.5% hit rate on n=374 that returns
-17.4%. Hitting three in five and still losing a sixth of the stake is the
juice, and it is the clearest argument in the dataset that a hit rate without a
price is not a record.

CAVEAT THAT HAS TO TRAVEL WITH THESE NUMBERS: only 582 of 1,453 graded
receipts carry a price at all (871 unpriced). So every ROI above rests on a
subset - MLB ml on 76 of 354, MLB total on 60 of 374 - and that subset is not
known to be random. The hit rates use the full n; the ROIs do not.

NEXT: decide whether the spread/total reads should keep publishing at all, or
route through the Fade filter (the one football spread edge that survived a
holdout). That is a product call, not a measurement.

### B89 - two receipts hold raw LLM markdown in their identity columns - **CLOSED, nothing to repair**

Found by B88's auditor refusing to classify them. Receipts 9878 and 9879
(2026-08-02, MLB, game_read, capture_mode=reconstructed) have the model's raw
output smeared across `market`, `pick_side` AND `pick_label`:

    market      '** ml  \n**side:** home  \n**line:** null  \n**call_text:** cleveland guardians ml ...'
    pick_side   '** HOME  \n**LINE:** NULL  \n**CALL_TEXT:** CLEVELAND GUARDIANS ML ...'

A parse that split on `**FIELD:**` labels and appended the remainder to every
field. 9879 carries `result = WIN`, so it counts in the POOLED record while
being unclassifiable by market.

CANNOT BE FIXED IN PLACE, and that is correct. A PATCH to those columns returns
HTTP 200 and changes nothing - `trg_freeze_receipt_identity` makes a published
receipt's identity immutable and only `result` writable. Verified again today.
The trigger is right; a published pick must not be rewritable.

OPTIONS, both needing a decision: void the two rows (removing one WIN from the
pooled MLB count), or leave them and accept that two of 1,453 are
unclassifiable. Volume is negligible; it is recorded so the next person who
sees the auditor skip them knows why.

---

# 2026-10-20 — ANDY'S QA PASS, LOGGED PROPERLY

He asked: "Are you keeping trakc of all the issues i bring so we can resolve
them?" Honest answer at the time: the backlog was current through B89, but this
day's QA list was being held in-session and nowhere else. That is the exact
failure mode this file exists to prevent, so every item he raised today is
below with a status, whether or not it is fixed.

Rule going forward: an item he reports gets a B-number in this file **in the
same turn he reports it**, before any work starts on it.

## Fixed today, waiting on a build

### B90 - NHL had no situational badges - **CLOSED** `e549ce1f`

"NHL doesnt have abdges, also." Not a rendering fault: NFL, NCAAF and NBA each
have a `*SituationalCard` and NHL had four stat cards and no chip row, so there
was nothing to render. `NHLSituationalCard` now renders first in `NHLSlot`.
Fields checked populated over 40 forward contexts before building on them:
back-to-back 40/40, rest_days 40/40, goalie 40/40, goalie_confirmed 40/40,
goalie_gsaa 38/40, pp/pk 40/40, away_consecutive_road_games 40/40. Chips:
back-to-back, rest edge (>=2d), goalie unconfirmed, elite/weak goalie (GSAA
+/-8), special-teams cross edge (>=8pp), road trip (>=4).

### B93 - NCAAF Games tab showed ONE game - **CLOSED** `e549ce1f`

"why only one game new mexico state is showing in ncaaf games tab". The 10-07
week-map fix was correct and the map loads fine; the KEY that read it did not
survive Hermes:

    new Date(t.toLocaleString('en-US', {timeZone:'America/New_York'}))

round-trips a Date through "10/21/2026, 8:00:00 PM" and re-parses it. V8
accepts that format so it works in node and in a browser; Hermes parses only
ISO 8601 and returns Invalid Date, so every component was NaN, the key read
"NaN-NaN-NaN", `_weekByDate` never hit, and every game fell through to the
7-day arithmetic that the comment directly above it says cannot reproduce CFB
weeks.

Measured today with 56 games live on the feed:

    thisSeasonWk = 8         (from the map - that call does not use this key)
    weekTodayLow = Wed 10-21 (Tue roll)  -> hides Tuesday's 3 games
    arithmetic:  10-21 -> wk 8  PASSES   -> NMSU @ Sam Houston   1 game
                 10-22 -> wk 9  hidden                           3
                 10-23 -> wk 9  hidden                           5
                 10-24 -> wk 9  hidden                          44

Exactly one game, and it is the one he named. 53 games were hidden by a string
parse. Fixed with `toLocaleDateString('en-CA')`, which returns YYYY-MM-DD with
no re-parse and is what the other ~20 ET-date call sites in the file already
use. This was the only round-trip in `app/index.tsx`.

LESSON WORTH KEEPING: `new Date(x.toLocaleString(...))` is a V8-only idiom. It
cannot be caught by a node check, a type-check or a lint - only on device. Grep
for it before shipping any date logic.

### B94 - The Fade still rendered the Split - **CLOSED** `b65a2a7d` + `e5e9d2eb`

He reported this FOUR times before it was found ("the plsit data is still in
the dafe in expo", "DO YOU NEED A PICTURE?"). Two separate leftovers: the
`section_note_split` beta banner, and the entire `<LineMovementTab/>` component
still mounted under the Fade tab - which is where "strongest signals right now"
and "minority dissent" were coming from. Renaming a tab is not converting it.

### B95 - The Fade's backtest record was invisible - **CLOSED** `e5e9d2eb`

"Why is the record 1-1 and we dont see the backtested record". It WAS
rendering - 9px, muted, italic, below the filter line - while a 1-1 live record
sat above it at 28px. Live n=2, backtest n=107. Now its own block at equal
weight, still labelled BACKTEST / NOT A LIVE RECORD so the two never merge.

### B105 - migration 19a could not be applied - **CLOSED** `c554166b`

`ERROR 42P17: generation expression is not immutable`. My bug:
`checked_date date GENERATED ALWAYS AS (checked_at::date) STORED`. Casting
timestamptz to date depends on the session TimeZone, so the expression is only
STABLE, and a stored generated column requires IMMUTABLE. `AT TIME ZONE` does
not rescue it either - DST rules can change under it. Replaced with a DEFAULT,
which is evaluated once at INSERT and has no such restriction.
**ANDY STILL NEEDS TO RUN IT** - the fix is committed, the table is not created.

## Open - diagnosed, not fixed

### B91 - NBA situational badges do not show - **CLOSED** `327743af`

Asked twice ("why dont i see nba badges", "NBA badegs arent showing hence why i
asked"). `NBASituationalCard` EXISTS and is mounted, so unlike B90 this is a
data or field-name question, not a missing component. Leading suspect: the NBA
ctx select does not request `home_starters_out` / `away_starters_out`, so the
chips that depend on them can never fire. NEXT: dump one `nba_game_context` row
field-by-field against the chip conditions and find which conditions can fire
at all.

### B92 - NCAAF situational badges do not show - **CLOSED** `327743af`

"also dont see ncaaf badges either." `NCAAFSituationalCard` exists and is
mounted. Same shape of question as B91 - which chip conditions can fire on a
real row. Suspect temp/wind/conference fields unpopulated for FBS rows. NEXT:
same per-field dump for `ncaaf_game_context`.

### B96 - The Fade's loading text still says "reading line drift" - **CLOSED** `e5e9d2eb` + `dee1ad2e`

"when it loads when you first clik into the fade the loading things says
reading line drift (form the split)." Cosmetic, one string, same family as B94.

### B97 - The Fade must be eligible in EVERY sport - **ANSWERED, blocked with a named unblock** `e111f893`

"The Fade shoudl be elginble in every sports also." Currently football-only
because the backtested edge (+12.4pp, n=107) is football spreads. Blocker to
check first: `close_spread` ORIENTATION per sport - B71 found Red River's sign
inverted, and a fade keyed off the wrong sign publishes the wrong side. Verify
MLB/NHL/NBA sign before widening, and publish any non-football leg as its own
labelled line so the football record stays clean.

### B98 - "building sample" blocks in Receipts - **CLOSED** `68ce6b23`

"YEah as mentioned dont love the bbuidling sampel thoguths? dont be afreaid to
figth abck on this just dont love it peronally." Pushback owed: the block is
what keeps a 3-5 record off the screen as if it meant something, and hiding the
surface entirely would read as us concealing a loser. Middle option worth
proposing: show the raw W-L with no rate and no tier until n>=30, which is
honest without looking like a gate.

### B99 - verify the Games tab filters across all sports - **CLOSED** `69680b0c` + `fa7f8740`

"time the conviction best edge and the prime only and strong only one, verify
workign as designed." Not yet checked. Note B93 means any filter verification
done on NCAAF before `e549ce1f` was testing against a broken game list.

### B100 - NBA line movement has no opening line - **CLOSED** `dee1ad2e`

"invertgate no open line in nba line movmeent in game detail, do toher sport
record opening lines as well?" Second half is the real question: which sports
actually run `freeze_opening_lines()` into `*_game_context`. NBA likely never
had the step wired at launch.

### B101 - NBA Recent Schedule is missing ATS and O/U - **NOT A BUG, explained** `dee1ad2e`

"Recvent shceudle in NBA is missing ATS and O/U why?" Expect the same cause as
B100 - no stored closing number to grade against on opening night.

### B102 - verify NFL stat accuracy in the new build - **VERIFIED CLEAN**

"want to verify accuracy of nfl stats in new buidld." Nothing done. Should be
done against a primary source, not against our own derived tables, and should
cover the B68 matchup rows (off rush yds vs opp def rush yds allowed, points
scored vs points allowed) since those are new.

### B103 - NHL and NBA props are not live in Prop Jerry - **ANSWERED, by design**

"Add to list NHL and NBA props radiness why arent they live in propp jerry
yet?" Known context: NHL props carry a documented over-bias and are marked
info-only, and NHL props could not join their game (10-03). Needs a readiness
call per sport, not a blanket switch.

### B104 - today's Sweat Card: NHL spreads, and no NBA - **CLOSED** `d3c4cecf`

"Todays Sweat Card NHL spreads, how do we do woth those? why are those p[lays
on sweat card... we definitly should one prop and one nba jerry pick." Two
parts: justify NHL spreads on the card against their own record, and whether
the composer can reach NBA at all on opening week.

### B106 - grade_money_flow.py is written but never run or committed - **CLOSED** `6f2d8aef`

Written today, the vig bug in it fixed (it had used `100 - favourite_implied`
as the fade breakeven, which is the vig-free complement, not the price of the
side taken - that flatters every fade). It has not been run to completion, has
no row in `signal_registry`, and is not in git. Until it is, "rolling stats x
money flow" is unanswered.

## Resolved today, logged so they are not re-opened

- **The Sharp is bleeding** - he was right. -16.48u over the last three weeks.
  Also found: the published record is flat-110 (+79.91u) while real prices give
  +3.25u. Those are two different numbers and only one is real.
- **POTD showed 5 pending** - now 0. Root cause was the partial grade mirror:
  it drops None fields, so `result` wrote while `game` and `lean` did not.
- **NHL registry note was stale** - `refresh_calibration_notes.py` now rewrites
  the NHL and UFC banners nightly and graduates a sport out of "calibrating" on
  a rule (n>=250 and >=45 days), so he never has to ask again. Scheduled in
  `nightly_cross_sport.sh` as of `3178cd81` - the first commit shipped it
  unscheduled, which is the "works by hand, in no cron" failure again.
- **Records pooled spreads/ML/totals** - split per market in B88. Spreads and
  totals lose, moneylines hold.
- **The model panel was one voice** - 27 duplicate pairs, NHL and NCAAF totals
  at 100% side agreement. Each model now derives its own number from its own
  inputs, refit nightly into `jerry_cache voices_<date>`, and duplicate tiles
  collapse to one voice at VOICE_EPS 0.26.
- **Engine weighting by track record** - 49 model/market rows now carry an
  earned weight in `signal_registry`, graded per market with price-implied
  breakeven on moneylines (flat 52.38% credited favourite-picking as skill).

## The build is the bottleneck

B90, B93, B94, B95 and B86 are all fixed in the repo and invisible on his
device. There is no OTA path (`expo-updates` is not installed), so every one of
them needs a binary. That is now five user-facing fixes queued behind one
build.

## 2026-10-20 late - B91 and B92 closed, and they were bigger than badges

Both were data questions rather than missing components (that was B90, NHL).
The audit found more than the badges he asked about.

**B91, NBA - five names absent from one SELECT killed two entire cards.**
`home/away_starters_out`, `home/away_abbrev` and `home/away_injury_impact`
were read by the cards and never fetched:

    starters_out    NBASituationalCard   15 chips over 28 games, none could fire
    abbrev          NBAInjuriesCard      queries nba_injuries BY ABBREV -> with
                                         none it returned early, then returned
                                         null. NEVER RENDERED.
    abbrev          NBAFourFactorsCard   same, nba_team_stats by abbrev. DEAD.
    injury_impact   the line under Injuries

Each column was verified to EXIST and be POPULATED before being added, because
an explicit select naming a missing column returns 400 and this app reads a 400
as "no context" - the blind fix would have blanked all of NBA. abbrev 28/28,
injury_impact 28/28, starters_out 15 non-empty sides of 56, nba_injuries has
rows.

**And unlocking it would have shipped a wrong number.** The impact line
formatted `impact * 100` as a percentage, but `*_injury_impact` is POINTS PER
GAME LOST summed over players listed OUT - 0 to 44.7 tonight, nonzero median
11.8 - so LAC would have read "4470% of typical starter value", and the >= 0.2
gate (written for a fraction) fired on two tenths of a point. Now per side, in
ppg, named, floor 5.0, star-out call at 15.0 ppg reusing
`shadow_nba_star_out.py`'s own threshold. `away_injury_impact` had never been
read at all.

**B92, NCAAF - the badges were never broken.** 16 of the 20 rows with context
this week would render a chip. He saw none because B93 left exactly one game on
the tab and that game, NMSU @ Sam Houston, is a Wednesday kickoff that hit two
real bugs this commit fixes:

1. ET came from a fixed `-4h` offset. Correct today, WRONG from Nov 1 when EDT
   becomes EST - a noon EST kickoff is 17:00Z, read as 13:00 ET, outside the
   10-13 Big Noon window. Every slot chip would have silently stopped matching
   for the last month of the season and the bowls. Now Intl in
   America/New_York. Verified: 10-24T16:00Z and 11-21T17:00Z both give Big Noon
   now; the old code gave hour 13 and no chip for the second.
2. No midweek slot at all. The comment lists "Thu/Fri primetime = Weeknight"
   but only Thu/Fri/Sat were handled, so Tue/Wed MACtion got nothing.

NOT BUGS, measured so nobody re-investigates:

- ~~`temp`/`wind`/`dome` 0/20 because OPENWEATHER_API_KEY is unset~~ **WRONG,
  and corrected within the hour - see B107 below.** The key IS in GH secrets.
  I repeated a stale memory note instead of checking. Real cause was my own
  10-09 guard. Fixed `b14f0ffe`; 10-21/10-22/10-24 now fully populated.
- `ncaaf_game_context` holds **14 of Saturday's 44** games on a Tuesday. That
  is the normal Wed-back/Thu-promote cadence, not a gap: last Saturday closed
  at 52/52. Forward dates past this week sit at 0 and fill in weekly.

LESSON, the third instance today of the same class: a card that reads a ctx
field the SELECT does not name renders blank with no error anywhere. Grep every
`ctx?.field` in a sport's components against that sport's select string before
believing a card is broken. The NBA audit took one script and found two dead
cards nobody had reported.

### B107 - NCAAF CI wrote ZERO weather rows for five weeks - **CLOSED** `b14f0ffe`

Andy: "What are you talking about weather api key we talked baout this
eysterday. Its in GH secret wtf are you talking about."

He was right and I was repeating a stale note. The key is in GH secrets and
both workflows pass it. **Never ask for it again.**

The 10-09 fix outlived its own premise. The source was migrated to
**Open-Meteo, which is free and needs no key** - `fetch_forecast`'s docstring
says so and names the 10-09 memory as the failure mode it removes. But
`if not OW_KEY: return 1` was left at the TOP of `run()`, so in CI it exited
before calling the keyless source, and `continue-on-error: true` made it green.

    date       rows   temp populated
    10-09         5        5     <- hand run
    10-10        46       44     <- same run's 7-day window
    10-13         2        2     <- hand run
    10-15         3        1     <- hand run
    10-17        52        0     <- SATURDAY. CI ran. Wrote nothing.
    10-24        14        0     <- until today

Every populated row came from a hand run. `workflow_heartbeat` shows
`ncaaf_pipeline` start AND end on every cron slot including Sat 10-17 12:15Z,
so the pipeline was healthy - one step returned early. 52 Saturday games
shipped with no weather, and wind-UNDER (55.6%, n=1,444) is the only positive
totals lens we have.

Proven both ways: with the key stripped, before gave "FAILING LOUDLY" and 0
games; after gives 16/16 and exit 0, every row `open_meteo`. The real run
filled 10-21 1/1, 10-22 1/1, 10-24 14/14.

FIX, and the generalisable part: **a precondition check must not sit in front
of a path that does not need it.** Judge the RESULT - reachable games that came
away with nothing exits 1 - which is the shape the NFL twin already had, and
why NFL never broke (10-11 13/13, 10-18 12/12, 10-25 12/12 in-window). NFL's
copy of the branch demoted to a note; it was returning 1 on runs that had just
written every game.

Also stopped stamping `weather_source='openweather_open_meteo'` on rows
Open-Meteo served. VERIFY: `select game_date,count(temp)` on
ncaaf_game_context for the coming Saturday after the Wed cron - it should be
full, not 0.

TWO OPEN QUESTIONS this raises, not yet done:
- **Which other steps are green-on-no-op?** `continue-on-error: true` plus an
  early `return` is invisible. Sweep every workflow step that carries it.
- **Past Saturdays cannot be recovered.** Weather is a forecast; 09-26,
  10-03 and 10-17 (171 games) will never have it, so the wind lens cannot be
  graded on those weeks. Do not backfill a current reading onto a past game -
  that is the 10-13 NULL-backfill-with-the-price mistake in another costume.

### B108 - ~400 workflow steps can fail GREEN, and nothing checks the outcome - **CLOSED** `97a4c644` + `916cffee`

Opened by B107, which hid for five weeks behind exactly this. Count of steps
carrying `continue-on-error: true`:

    mlb_pipeline.yml        140 of 177
    nfl_pipeline.yml         69 of  88
    ncaaf_pipeline.yml       55 of  66
    nba_pipeline.yml         37 of  39
    ncaab_pipeline.yml       32 of  44
    nhl_pipeline.yml         32 of  36
    daily_card.yml           10 of  19
    ufc_pipeline.yml         11 of  22
    multisport_line_poller    7 of  11
    mlb_grade_overnight        6 of  10
    mlb_oddscrowd_refresh      5 of   9

The flag itself is right - one scraper outage must not kill a slate. The gap is
that **nothing downstream checks whether the step actually wrote anything**, so
a step that returns early is indistinguishable from one that succeeded.
`workflow_heartbeat` answers "did the workflow run", which was green through all
five weeks of B107, and says nothing about what any step produced.

The sweep for B107's exact shape came back CLEAN, and that is worth recording:
26 scripts return early on a missing key, and in every other case the key gates
the ONLY path (an LLM call needs ANTHROPIC_API_KEY, an odds pull needs
ODDS_API_KEY), so returning is correct. The weather pair was unique in having a
keyless PRIMARY source behind a key-gated guard. So the remaining exposure is
not "guards in the wrong place" - it is **no outcome assertions**.

**BUILT** `97a4c644` - `mlb_pipeline/assert_slate_freshness.py`. Checks the
NEXT SLATE of every in_season sport and fails on a DARK COLUMN: zero populated
rows out of N on a slate that HAS rows. Partial fill is reported and does NOT
fail, because football context fills across the week on purpose (14 of 44 on
the Tuesday, 52/52 by Saturday) and failing on partials would get it muted.

Wired two ways on purpose: a **detector** in the nightly (6:30am ET, before the
daily sports build, so "no rows" is expected then and must not fail it), and a
**hard step with NO continue-on-error** at the end of ncaaf_pipeline and
nfl_pipeline. This is the check that catches the advisory ones failing
silently, so making it advisory would defeat it.

VERIFY: `python assert_slate_freshness.py` - exit 1 and a DARK line on the
pre-fix weather state, exit 0 clean now across MLB/NBA/NFL/NHL/NCAAF.

THREE MISTAKES IT MADE FIRST, kept because each is a trap for the next check:
1. It flagged NCAAF temp 0/3 - a FALSE POSITIVE worth more than the catch.
   Those games had already kicked off and the weather window is
   `kickoff_utc >= now`. A started game's row can never be written, so
   asserting on one only teaches us to ignore the script. Started rows are now
   dropped via each sport's own time column.
2. MLB at 3 days said "no rows" (true: ALCS ended 10-19, World Series opens
   10-23). Raising to 6 was ALSO wrong - MLB has no rows 6 days out and should
   not, because `game_context.py` is same-day by construction. **A horizon
   longer than the builder's reach manufactures a permanent warning.**
3. Weather must be asserted on OUTDOOR rows only, per sport's own column, and
   skipped for MLB/NHL/NBA which have no weather columns at all.

CLOSED OUT in `916cffee` - Andy: "parlty closed is unaccetable". Both gaps:

**COVERAGE, not just population.** Each next slate is counted against an
INDEPENDENT upstream schedule - statsapi.mlb.com and api-web.nhle.com
(keyless), the Odds API for NFL/NCAAF/NBA/NCAAB:

    0 rows while upstream lists games  -> FAIL (the builder did not run)
    short of upstream                  -> WARN with both counts
    upstream unavailable               -> said out loud; population still runs

The old "no rows in the window" shrug is now a FAIL when there was a slate to
build, and still names a real dead week correctly: MLB has no rows today and
statsapi agrees (ALCS ended 10-19, World Series opens 10-23).

PROVEN, not asserted - pointed at 2026-11-15 where api-web.nhle.com lists 5
games and nhl_game_context has none:
    `NHL: nhl_game_context has NO rows for 2026-11-15, but the upstream
     schedule lists 5 game(s) - the context builder did not run`

**AND COVERAGE'S FIRST VERSION WAS WRONG** - the part worth remembering. It
reported NHL "3/11 SHORT 8" and NBA "3/2", both artefacts of comparing two
different populations: our side had started games removed (correct - a started
game's row can no longer be written), while MLB/NHL upstream counts the whole
day and **the Odds API drops games once they begin**. A number nobody can act
on is worse than no number. Coverage now runs only on a slate with nothing
underway, counts every row held for that date, and where there is no
start-time column at all (MLB, NBA) it says so rather than printing a figure.

**ALL SIX PIPELINES ASSERT**, every one without continue-on-error: ncaaf, nfl,
nhl, nba, mlb, ufc. Verified each `--sport` invocation exits 0 as the workflows
call it.

**UFC needed a different check and got one.** No `*_game_context` exists -
only `ufc_fight_results`, resolved fights, zero forward rows - so the next
card's `jerry_reads` are asserted instead. Price is deliberately NOT asserted:
all 8 forward reads on the 10-24 card are `call_market='pass'`, and a pass
carries no side and no price by design. The 8-of-8 pass rate is reported as a
note - that is **B83's symptom**, independently corroborated here, not this
script's failure.

REMAINING EXPOSURE, stated in the file so a green run is not overread: the
~400 `continue-on-error` steps are untouched (the flag is right - one scraper
outage must not kill a slate; what changed is that a dark OUTCOME is now
visible even when the step lies), and nothing here proves a row's VALUES are
correct rather than merely present. **A wrong close_spread passes every check
in this file** - that is B51/B71 territory and a different kind of test.

## 2026-10-20 night - B96 / B100 / B101 resolved, and one bug retracted before filing

**B96** was already fixed. "Reading line drift..." exists only in
`LineMovementTab.tsx`, so unmounting it (`e5e9d2eb`) took the string. The note
left behind claimed the component's fetches were "still used elsewhere" -
wrong, nothing renders it, so the dead import is gone too. File kept.

**B100 was real and is two different problems wearing one symptom.** Measured
on rows from 10-10:

    MLB     open_spread  12/12    open_total  12/12
    NFL     open_spread 207/207   open_total 207/207
    NCAAF   open_spread 128/128   open_total 128/128
    NHL     open_spread   0/118   open_total  83/118
    NBA     open_spread   0/70    open_total   0/70

- **NBA never wrote one.** The columns exist; `nba_game_context.py` had zero
  references to them. `preserve_opens()` added, copied from NFL rather than
  invented, because the naive version fails invisibly: this builder
  reassembles every row from live event data each run, so a plain
  `open = close` mirror overwrites the true opener with today's close on every
  pull and the column looks populated while meaning nothing. Mirrors only into
  an empty slot, always prefers the stored value, and REFUSES to mirror if the
  lookup fails - `freeze_opening_lines` silently discards the repair and
  returns 200, so a bad first write is permanent.
- **NHL was never missing one.** It records the opener as `open_puckline`;
  `open_spread` is a dead column there. The strip already fell back to it, but
  `movedSpread`/`movedTotal`/`movedML` did not, so the "line moved" badge was
  permanently undefined for NHL and read as "no opener on file".

**B101 is NOT a bug.** Detroit's one regular-season row renders
`sp=-1.5/won  tot=227.5/over` correctly; the other four are OCTOBER PRESEASON
games with no closing line, so there is nothing to grade. On the regular
season's first day, four of every team's last five games are preseason. Ruled
out along the way: team names match exactly, `seq=1` really is most recent, and
the season filter does not apply to NBA. Self-resolves - today's slate has a
line on 3 of 3. The card now names the reason instead of showing five blanks.

### THE RETRACTION, recorded because the shape was convincing

Past NBA dates had `close_spread` on **6 of 59** rows while forward dates were
full. That looks exactly like a later pull nulling a stored line, and the
mechanism is real: the builder sends the whole row and the odds feed drops
games once they start. **Checked before writing it up - no wipe exists.** Every
one of those rows is `season_type='preseason'`, `nba_game_results` shows the
same sparse pattern, so they never had a line to lose. MLB/NFL/NCAAF are 100%
on past dates, NHL 97%. The guard added for opens makes the overwrite
impossible for open_* regardless.

### B109 - preseason games appear in user-facing L5 records - **CLOSED** `b7aff6e6`

Surfaced by B101. `team_recent_games` has NO `season_type`, and neither
`nba_game_results` nor `nhl_game_results` carries the column, so preseason
cannot be excluded from an L5 without ADDING AND BACKFILLING it.

It should be excluded. A preseason game inside a user-facing record is exactly
what made the NHL record read **100% preseason** on 09-30
(`project_nhl_preseason_record_930`), and that cost trust. Right now an NBA L5
is four preseason games and one playoff game from last May.

**CLOSED `b7aff6e6`, and it needed NO migration at all** - the first read of
this was wrong on both counts.

THE TRAP: a season filter does not exclude preseason. The matview stamps NBA
preseason games with season **2026**, the same season as the regular games, so
filtering on season keeps every one. Boston's 10-16 preseason game and tonight's
opener are both season=2026.

**The boundary is a DATE, and it was already in the table.**
`sport_registry.season_start` is populated on every sport - NBA 2026-10-20,
NHL 2026-09-29, NCAAB 2026-11-03 - so no heuristic, no 45-day window, no
season_type column and no backfill.

MEASURED BOTH WAYS, which is what shows it is surgical:

    NBA  Boston    before 5 rows / 1 graded  [10-16,10-14,10-10,10-08,05-02]
                   after  0 rows
         Detroit   before 5 rows / 2 graded  [10-16,10-14,10-10,10-05,05-17]
                   after  0 rows
    NHL  Carolina  before 5 rows / 5 graded  ->  after IDENTICAL
         Washington                          ->  after IDENTICAL

NHL costs nothing (its L5 was already all regular season). NBA correctly
empties to "No games logged yet this season" on opening day and fills from
tomorrow - an honest empty state beats five rows of blanks reading as broken.

Scoped to NBA/NHL/NCAAB; NFL and NCAAF already filter by season, MLB has no
preseason in results. Team tabs only. FAILS OPEN if season_start is missing.

**The cache key moved to `sport_registry_v2` with the column.** `withCache`
holds that row set for 30 minutes with staleWhileRevalidate, so adding
`season_start` under the old key would have served rows lacking it and the
field would read undefined for half an hour on every warmed device -
indistinguishable from the feature not working. Any change to that select list
changes the key.

VERIFY after the build: an NBA Recent Schedule L5 lists only games on or after
2026-10-20, and NHL's is unchanged.

## 2026-10-20 · B106 CLOSED - money flow graded, and nothing in it is a fact

Three things were wrong with the first pass, all caught before anything reached
the registry the engine reads:

1. **68% of every split was being discarded.** "no context row: 1967" was NOT a
   join bug - snapshot ids match context at NFL 100%, NCAAF 99%, NHL 100%, NBA
   100%. MLB matched **3%** because `mlb_game_context` retains only 28 rows
   against 675 MLB games in line_snapshot. `mlb_game_results` keeps 4,833 rows
   with the closing line AND final score and matches on **99%**. Falling back
   to it took gradeable rows 921 -> **2,869** and MLB 48 -> **1,993 games**.
2. **The early/late split was printed and ignored.** The docstring says the
   halves are shown so a one-half bucket "is visible as such", then the tier
   used the pooled edge alone - handing weight 1.00 to NCAAF public_heavy_65 rl
   at 58.5% with halves **51% / 66%**. VALIDATED now needs both halves to clear.
3. **`wilson()` was defined in the file and never called.** NCAAF
   public_heavy_70 rl still passed at 1.00 on 33-20: 62.3%, n=53, lower bound
   **49.3%** vs a 52.4% breakeven. VALIDATED now needs the LOWER BOUND to clear.

**RESULT: nothing is VALIDATED.** Every bucket in every sport has a CI spanning
breakeven, so the ceiling is **0.50** - shadow weight, not fact. 28 rows
written. The interval prints on every line and is stored in every description,
so a tier can be checked rather than trusted.

WHAT THE DATA SAYS, direction never assumed:

    MLB   spread  FADE the public   54-55%  n=248-453, both halves clear
    MLB   total   FOLLOW the money  55-58%  n=157/100 on money_vs_bets
    MLB   ml      NOTHING WORKS     every bucket REJECTED on price-implied
                                    breakeven - independent confirmation of
                                    "never test public money on the ML"
    NCAAF spread  FADE the public   54-62%, but one half or the CI fails
    NCAAF total   NOTHING           at or under breakeven everywhere
    NFL/NHL/NBA   n too small to grade

**MLB fading on the spread while FOLLOWING on the total** is the sharpest
version yet of `project_split_flags_opposite_by_sport_1012`: the direction
flips not just by sport but by MARKET WITHIN a sport. No global money-flow
treatment can be right.

Scheduled in the nightly as a detector on the first night it existed.

## 2026-10-20 · B83 / B99 / B102 / B103

### B83 — the UFC premise was wrong, and the obvious fix would have lost money

The engine has not stopped having opinions. On the 10-24 card 7 of 8 fights are
genuine coin flips (p_winner_a 0.47-0.55, ev_tier SKIP). The 08-21 isotonic
calibrator is NOT the cause either: October's median favoured-side probability
is 0.667 with 21 of 33 fights at or above 55%.

The synthesis layer DOES override: Volkanovski/Evloev came back p_a=0.3192,
conviction 36, tier_winner STRONG, **ev_tier PRIME** — and shipped as a PASS.
6 of 19 PRIME model picks produced no published play.

**That looks like `project_jerry_override_costs_the_edge_1007` and it is the
opposite.** All 95 gradeable picks at real median decimal prices:

    ALL                51-44   53.7%   ROI  -4.7%
    ev_tier PRIME       7-10   41.2%   ROI -37.5%   <- the override SAVED money
    ev_tier STRONG      8-2    80.0%   ROI  +8.6%
    tier_winner PRIME  19-10   65.5%   ROI -13.5%   <- wins often, loses money

n=17 on PRIME is far too small to call proven; the honest claim is that there
is NO evidence PRIME wins and the point estimate is -37.5%. **No gate changed.**
The user-facing defect IS fixed: a fight-week card showed zero plays under a
banner reporting 61.1%. The note now says "No play on the 2026-10-24 card: the
model rates all 8 fights too close to price... A pass is a position."

STILL ANDY'S CALL: whether UFC should publish at all. Also still true —
`public_receipts` holds 0 UFC rows, and only 95 of 155 picks are gradeable (32
have no result row, 28 no odds).

### B99 — the filters were fine, the SORTS were sport-blind

    time        CORRECT      Prime Only  CORRECT      Strong+  CORRECT
    CONVICTION  BROKEN on 5 sports       BEST EDGE    BROKEN on NHL + NBA

Conviction read `mlbGameContext` ONLY, so every non-MLB game resolved
primary_play undefined, tier '' and tierWt 0 — the whole slate tied at zero and
fell through to the client score. Hidden by that: NFL 179 tiered rows (2 PRIME,
10 STRONG), NCAAF 80 (37 STRONG), NHL 82 (11 STRONG), NBA 25. Best Edge's own
map chain stopped at NCAAF so NHL/NBA LR signal was permanently 0.

**Same sport-blind-map defect already fixed TWICE in this file** (the 08-25 and
09-28 notes inside Prime Only). It recurred because each call site kept its own
copy of the map chain. One shared resolver now.

### B102 — NFL stats verified against a primary source, clean

Checked `team_stats_rolling` against **ESPN standings**, not our own tables:

    30 of 32 teams match points/g and points-allowed/g EXACTLY
    max difference 0.03 (one-decimal rounding)
    teams off by more than 0.6 pts/g: ZERO

All 8 matchup stats present on all 32 teams, none missing. The two apparent
gaps (LAR, WSH) were MY comparison script: we store `LA`/`WAS`, ESPN uses
`LAR`/`WSH`. Join keys also verified to match per sport — NFL abbrev-to-abbrev,
NCAAF/NHL/NBA full-name-to-full-name.

ONE LATENT RISK, not a current bug: the card queries by
`ctx?.home_team || game?.home_team`. For NFL ctx gives `ATL` while the odds
feed gives `Atlanta Falcons`, so on a game with NO ctx row the card would query
a full name against abbreviation keys and come back empty.

### B103 — not live by design, and it is not a one-line change

Both tables exist and are CURRENT: `nhl_pipeline_props` latest game_date
2026-10-20, `nba_pipeline_props` 2026-10-22. The app's `PROP_SPORT_REGISTRY`
holds only MLB and NFL; NBA is commented out and NHL was never added.

The registry comment claims adding a sport is "1 registry line". It is not —
MLB and NFL both read a server-gated VIEW, and neither exists for the others:

    v_mlb_props_publishable   HTTP 200        get_todays_pipeline_props        200
    v_nfl_props_publishable   HTTP 200        get_upcoming_nfl_pipeline_props  200 (172 rows)
    v_nba_props_publishable   HTTP 404        get_todays_pipeline_props_nba    404
    v_nhl_props_publishable   HTTP 404        get_todays_pipeline_props_nhl    404

So each needs a view, an RPC and a publishable gate.

**AND FOR NHL THE EVIDENCE SAYS DO NOT.** `project_nhl_prop_over_bias_1009`
(documented over-bias, marked info-only), `project_nhl_prop_inputs_lose_to_price_1003`
(inputs lose to price), `project_nhl_props_cannot_join_their_game_1003`.
Publishing a surface already measured to lose is the opposite of the discipline
in this file.

**For NBA it is opening week with zero graded prop history**, so there is no
basis on which to set a gate at all. Recommendation: neither goes live now; NBA
becomes viable once it has graded history, NHL only after the over-bias is
fixed.

## 2026-10-20 night · B104 / B97 / B87 / B89

### B104 — NHL pucklines off the card, and that removes NHL from the card

    NHL ml      96-74   56.5%   ROI  -4.3%  (124 priced)
    NHL rl       5-10   33.3%   ROI -29.7%  ( 14 priced)   <- was on the card
    NHL total     3-1   75.0%   ROI +33.6%  (  3 priced)

`project_nhl_rl_market_not_viable_1003` called the puckline dead on 10-03 and
the composer had NO market gate to act on it. One added,
`CARD_INELIGIBLE_MARKETS = {('NHL','rl')}`.

**⚠ CONSEQUENCE, not buried:** of NHL primary_play rows reaching PRIME/STRONG —
the only tiers the cross-sport builder takes — it is **rl 20, ml 0, total 0**.
Every card-eligible NHL pick is a puckline, so this removes NHL from the Sweat
Card entirely until the engine starts tiering the market that works. That is
the deeper defect (`project_nhl_picks_no_market_1001`) and is NOT fixed here.
No NHL pick beats a -29.7% one, so the gate stands. Scoped to the card only —
NHL rl still generates, reads, shows in the Steam Room and accrues a record,
which matters at n=14.

NBA rl deliberately NOT gated: 3 of 4 card-eligible NBA picks are pucklines
too, but NBA has no graded side history, so there is nothing to gate on.

TWO IMPLEMENTATION MISTAKES WORTH KEEPING: the gate first sat only in `add()`,
which turned out to receive ONLY POTD, DotD and props — NHL game-side picks
take a separate cross-sport builder and would have sailed past it. **A gate
that cannot fire is worse than none**, because it reads as covered. And a skip
counter using `'_skipped_market' in dir()` to self-initialise in a nested scope
was removed — that is the UnboundLocalError class that cost a full NFL Sunday.

### B97 — the Fade's sign is fine; the DIRECTION blocks it

The Fade is already sport-aware and already correct: `_HOME_HANDICAP_SIGN`
verified against line_history, and it SKIPS unlisted sports rather than
guessing. Both signs independently re-confirmed by two more methods:

    sport    n      MAE -cs/+cs     home win% cs<0/cs>0   convention
    MLB    2132     3.52 / 3.89     58.0% / 45.5%         HANDICAP   (+1)
    NFL    7338    14.60 / 10.26    35.4% / 67.4%         HOME-MARGIN(-1)
    NCAAF  6546    11.97 / 28.73    78.7% / 29.4%         HANDICAP   (+1)
    NBA    2398    10.85 / 17.51    68.8% / 33.1%         HANDICAP   (+1)

NFL genuinely stores the OPPOSITE convention to every other sport
(`project_close_spread_sign_bug_914`, still true). NCAAF also confirmed on the
CONTEXT table the Fade reads: cs<0 → home wins 84.4%, cs>0 → 26.4%, n=492.

**It still cannot be widened, because of direction not sign.** Tonight's
money-flow grading: MLB spread FADE (the only candidate), **MLB total FOLLOW**,
NCAAF total nothing, NHL/NBA n<30. Putting MLB totals on a surface called The
Fade publishes the side the data says to take the other way — an inversion with
a friendly name.

UNBLOCK CONDITION, named: MLB runline is legitimate once
`mlb_game_context` retention is fixed so its sign can be verified **on the
table the Fade actually reads**. The results-table sign is NOT transferable —
that is the entire reason the constant exists, and how B71 inverted Red River.

### B87 — it nags nightly instead of going stale

The banner says a fix is "in review for the next app update": true today, FALSE
the hour the build ships, and no version telemetry reaches the pipeline. Auto-
clearing on a date would delete the one thing NBA users need if the build slips;
silence is how a stale promise sits in front of paying users. So the nightly
scans EVERY registry row for build-promise phrasing and prints a warning, and
`--clear-build-promise NBA` does it in one command. Scanned across all sports,
not just the three the refresher OWNS — a scan limited to OWNED would have
missed the only row it was written for.

### B89 — nothing to repair, and the record is RIGHT

B89 proposed voiding one WIN or accepting two unclassifiable rows. Both halves
of the premise were wrong:

* **9878 is already `NO_ACTION`** — it was a PASS, so it correctly counts for
  nothing.
* **9879's pick is recoverable from the smeared text** ("cleveland guardians ml
  / home / conviction 66") and its WIN is **VERIFIED CORRECT**: on 2026-08-02,
  ARI 0 @ CLE 5. It picked Cleveland ML at home. The grade is right.
* The garbled columns are **not user-facing**: the app renders `surface_records`
  rollups, never raw `public_receipts` rows.

So the record is accurate, identity is immutable by design, and the only cost
is that an auditor must skip two rows — which B88's auditor already does. **Do
not "fix" these.** Changing a correct WIN to UNGRADEABLE would have made the
record wrong in the name of tidiness.

## 2026-10-20 · B98 CLOSED — floor kept, hero number changed. And B110 opened.

Andy invited the fight. He is half right, and the half he is right about is
bigger than it looked.

**THE n>=30 FLOOR STAYS.** At n=16 a 2SE band is ~+/-25pp. The rates currently
withheld include **37%, 36% and 46% on 27-28 picks**. Printing those big and
colour-coded tells a paying user a surface is BAD when we have not measured it
— the opposite of "we show the receipts". Same floor the engine uses.

**HE IS RIGHT ABOUT THE PRESENTATION, and the scale is the argument:**
**128 of 352 tiles with data (36%)** replaced their hero number with
`n=29 / building sample`. A third of the Receipts tab read as unfinished, and
the phrase sounds like we have not started rather than that we are being
careful. Worse it was a **CLIFF** — 16-13 at n=29 showed a non-number, then one
more graded pick flipped the same tile to a confident "55%" with no change in
evidence.

**FIX: change which number is the hero, not the floor.** The record IS data;
16-13 is a fact at any n and cannot overstate. A thin tile now leads with the
RECORD at 20px, the hint reads "rate at 1 more" instead of "building sample",
and the secondary line shows "29 graded" rather than repeating the record. The
tile looks finished because it is — there is just no rate yet, and the cliff is
gone because the record was always the honest number on both sides of it.

### B110 - the `ledger` surface reports ONE record for EIGHT sports - **RETRACTED, it is intentional**

Found while measuring B98, not fixed. `surface_records` returns the **identical
16-13 / n=29** for `ledger` under MLB, NFL, NCAAF, NHL, NBA, NCAAB, UFC **and
ALL**. One record repeated eight times is not eight records — a user switching
sports sees the same Ledger line every time and reads it as that sport's.

Same shape as B64 (surface_records cross-sport contamination), which was
partially corrected on 10-09. `window_key` is also NULL on every row, which is
B64's other half.

VERIFY: `select sport,wins,losses from surface_records where surface='ledger'`
should not return one repeated tuple. Decide whether the Ledger is inherently
cross-sport (parlays can mix sports, so ONE row keyed ALL may be correct and
the per-sport rows should simply not exist) or genuinely needs partitioning.
That distinction matters before anyone "fixes" the numbers.

## 2026-10-21 · THE QA CARD REVIEW — ROOT CAUSES, NOT SYMPTOMS

### B111 - NBA sweat score was INVERTED - **CLOSED** `b356569b`

`compute_sweat_score` used `abs(projected_spread + close_spread)` under a
docstring claiming projected_spread is positive-is-home and warning "do not
simplify it to a subtraction". True for NCAAB (where it was ported from),
FALSE for NBA: `nba_elo.elo_to_spread` returns `-diff / ELO_PER_POINT` and
says "NEGATIVE = home fav". Verified by calling it — LAL 1574.2 at home vs GSW
1480.9 returns -7.7.

Not inflation, **inversion** — the games where model and market agreed most
collected the MAXIMUM edge score:

    CHI @ TOR   ps -9.6  cs -9.5   sum 19.1 -> +25     true 0.1 -> +0
    GSW @ LAL   ps -7.7  cs -6.5   sum 14.2 -> +25     true 1.2 -> +6
    46% of rows too high; CHI @ TOR was the 10-21 Play of the Day

RESCORED on Andy's instruction: 13 of 15 games changed. CLE @ PHI fell from
**86 PRIME on a 1.4-pt edge** to 68 STRONG; MIL @ WAS rose from 73 to **86
PRIME on a genuine 7.0**; HOU @ SAS and NYK @ BOS fell to PASS.

FULL SIX-SPORT AUDIT, every value from the WRITER or the stored data:

    sport   projected_spread   market column                formula      verdict
    MLB     positive = home    close_spread   neg = home    SUM          correct
    NFL     positive = home    close_spread   POS = home    DIFFERENCE   correct
    NCAAF   positive = home    close_spread   neg = home    SUM          correct
    NHL     positive = home    close_puckline neg = home    SUM          correct
    NCAAB   positive = home    close_spread   neg = home    SUM          correct
    NBA     NEGATIVE = home    close_spread   neg = home    was SUM      FIXED

NBA was the only one wrong — the only sport whose projected_spread comes from
`nba_elo`. NHL verified and DOCUMENTED IN PLACE (`f2a252ee`) so nobody "fixes"
it into the mirror-image bug: 187/187 rows satisfy `margin == hg - ag`, and
close_puckline is a handicap (home win 64.7% when <0 vs 41.7% when >0).

### B112 - the V3 tile named the wrong team on every NBA game - **CLOSED** `b356569b`

Same root. The lens grid renders `abbrev3(r.m > 0 ? home : away)`. Correct for
NFL/NCAAF, wrong for NBA — the tile read "GSW 7.7" while Jerry's read said
Lakers -7.7. **Jerry was right.** Normalised at the row builder.

### B113 - numpy missing from two workflows - **CLOSED** `a8da3546`

60 of 73 step failures in 72h, one line. Three panel VOTERS plus
`audit_duplicate_votes` import numpy; `nightly_cross_sport.yml` and
`mlb_grade_overnight.yml` installed only requests/dotenv/bs4. All four died at
rc=1 in **0 seconds** every run. Exact correlation: the two workflows that
already installed numpy show zero voter failures. The panel has been running
without three voices, and the duplicate-vote guard was dead.

### B114 - a card that argues with itself now fails the run - **CLOSED** `6bfc6e3c`

The QA's headline ask was "add a pre-render consistency gate". The gate ALREADY
EXISTED and already fails the run on exit 2 — it reported 0/7 clean that same
morning because none of its checks compare a pick to the numbers beside it.
Added PRICE / COVER / FLIP / STALE to `consistency_watchdog`.

**I wrote three convention bugs into that check and caught all three by running
it** — FLIP firing on every away pick (side-relative vs home-relative line),
then on all five NFL games (`_CLOSE_HOME_POSITIVE` defined and never applied),
then STALE comparing magnitudes so `+5.5` against a `+4.5` board read as worse
when it is better. Zero false positives now, three real findings over 7 days.

### B115 - a published receipt did not lock the pick - **CLOSED** `f66da57c`

The rescore ran at 09:30 ET with `lock_active('NBA')` correctly False, and
rewrote IND @ NO from rl/HOME to total/UNDER — a pick **already published**
(receipt 3005502, "New Orleans Pelicans +3.5", 10-19, ungraded). The hour
window assumes publication happens at the lock hour; receipts are written by
other surfaces on their own schedules. **A receipt is the fact; the clock was a
proxy.** Lock is now the OR of the two, scoped to the current slate — any
receipt at all froze 22 of 33 forward games; receipt + game-today gives 11
locked / 22 writable.

**And the lock failed open on its own success message.** `context[field] =
published` sat AFTER a print carrying a lock emoji; on a cp1252 console that
print raises, control jumps to the handler, and the handler crashes on its own
warning glyph — so the lock silently failed open at the moment it had decided
to hold a published pick. Nothing cosmetic may sit between a decision and its
application.

Repair: IND @ NO restored from the same run's `_ensemble_all_markets.rl` entry
(the engine still rates it at conviction 52; total merely outranked it 54-52).
Blast radius checked across every forward NBA game — that was the only one.
`consistency_watchdog` now 0/8.

### STILL OPEN from the QA review

Injuries render two sources for opposite teams (list = LAL from stale
preseason `nba_injuries`, impact line = GSW from ctx) · four confidence scales
(Sweat 78 / Conviction 62 / CONF +1 / POTD 60) · stale-stat labelling and
source disagreement (pace 98.7 vs 99.0, net 2.5 vs -0.500) · hallucinated POTD
prose ("last five games" on day 2 of a season) · P2 layout items.

## 2026-10-21 · THREE FLAGS ASSESSED

### B116 - a fabricated 100% UFC signal was carrying weight - **CLOSED** `b036b8bb`

`ufc_pipeline_pick_b` sat in signal_registry at **100.0% on n=20, edge +47.6,
DISCOVERY, WEIGHT 0.50**, beside `ufc_pipeline_pick_a` at 0.0% on n=24. Not
reported as a finding, because a perfect rate and its mirror on a binary
outcome is a wiring error until proven otherwise.

One line:

    c['_winner'] = 'A' if str(winner).lower() == str(c.get('fighter_a')).lower() else 'B'

`ufc_picks.winner_actual` holds a SIDE CODE. Across all 140 graded rows: 65
'a', 75 'b', and **ZERO** matching fighter_a by name — so the equality was
false every time and the `else` made _winner 'B' for all 140. FIGHTER_B_ML
always W, FIGHTER_A_ML always L, and **65 of 140 fights (46%) graded
backwards** — every one where A actually won.

Fixed to accept the side code, and to return None rather than guess when it is
neither (a draw must not become a loss for A). Recomputed:

    ufc_pipeline_pick_a    0.0% n=24 wt 0.30 -> 46.5% n=43  -5.9  wt 0.00
    ufc_pipeline_pick_b  100.0% n=20 wt 0.50 -> 54.3% n=46  +1.9  wt 0.30

Then swept the registry for the shape: **zero remaining rows at 0% or 100%
with n>=10.**

### B110 - RETRACTED. The shared ledger record is intentional.

I filed this as cross-sport contamination. It is not — `compute_surface_records`
names `ledger` in CROSS_SPORT_SURFACES and documents why: "The Ledger ships
chalk parlays that intentionally pair, say, an NHL leg with an MLB leg; that
combo belongs to no single sport. These report the ALL aggregate under every
sport key so no caller can read a wrong-by-construction figure."

A defensible choice, and I was wrong to call it a defect.

**The residual question is Andy's, not a bug:** the premise holds for 34 of 267
ledger receipts. The other 231 are MLB-only and 2 are NFL/NCAAF, so an NBA user
is shown a 261-pick record that is 87% single-sport MLB parlays. The design
avoids a wrong-by-construction split by substituting a different wrong
impression. Worth a decision, not a unilateral change.

### B117 - overnight results sat unresolved, and it is the cadence again

Two NBA game-card picks from 10-20 were still ungraded ~17h after the games
ended. `nba_game_results` held rows for both with NULL scores.

NOT a missing resolver: `nba_resolve_results.py` IS scheduled
(nba_pipeline.yml:131) and worked by hand immediately, finalising all three
10-20 games. The cause is the same cadence gap found this morning for money
flow — nba_pipeline last ran 10-20 at 21:39 UTC, BEFORE the 23:40 tips, and
the 12 UTC cron lands ~4.4h late. **The 13 UTC slot added in `0d5e8325` covers
this too**, since it resolves yesterday in the morning rather than
mid-afternoon.

**AND THE PENDING PICKS SKEWED THE RECORD OPTIMISTIC.** Both ungraded picks
were losses, so yesterday's reported numbers were materially better than
reality:

                      reported (2 pending)      actual (fully graded)
    NBA game_read     1-0   100.0%  +90.9%      1-2   33.3%  -36.4%
    ALL game_read    10-5    66.7%  +19.5%     10-7   58.8%   +3.6%
    market ml         7-3    70.0%  +17.2%      7-5   58.3%   -2.4%

Same lesson as the 10-13 ledger backlog, opposite direction (that one skewed
to WINs). **A record quoted with picks still pending is not a record.** Any
surface summary should state the pending count beside it, which the perf query
now does.

---

## 2026-10-21 · RLS, disk IO, and the prop scoring layer

### B1 · **CLOSED — verified stale, and the residual is now safe**
The grep in the original entry returns nothing: `EXPO_PUBLIC_KENPOM_KEY` and
`EXPO_PUBLIC_BDL_API_KEY` are gone, and all four balldontlie call sites were
emptied 09-22 ("I dont pay for it anymore lets just delete it"). The only
`EXPO_PUBLIC_*` values still bundled are the Supabase URL + anon key and the
RevenueCat key — all three of which must ship to work.

What made the anon key dangerous was not that it ships, it is that **RLS was
disabled schema-wide, so it could WRITE**. Closed today — see B118.

### B2 · **CLOSED — verified stale**
`season: 2025` and the literal `player_ids` went with the BDL removal.
`fetchPlayerStats` is now a no-op comment block.

### B118 · 🚨 The shipped anon key could rewrite `public_receipts` — **CLOSED**
RLS was disabled across all 190 public tables. The anon key ships in the app
bundle and is extractable, and it was not limited to reading: verified from
outside with that key via DELETE/PATCH filtered to zero rows (nothing
altered), returning 200 instead of 401 on `public_receipts`,
`prompt_templates`, `feature_flags`, `sweat_tier_config` and
`user_note_dispatch_log`.

Anyone with a key pulled from an installed copy could have silently rewritten
the graded record.

Closed over `20261021c` through `g`: RLS on all 190 tables, read policies
derived from the client's own `.from()` calls (plus `ufc_picks`, reachable
only through a SECURITY INVOKER RPC), write-only policies for the two tables
the client writes and never reads, and the 21 vestigial
`FOR ALL USING (true)` "write anon" policies dropped — justified by
measurement: 149 pipeline scripts use the service-role key, exactly 1 used
the anon key and that was the verifier written to test this.

`verify_rls.py --verify` returns **PASS**: 190/190 RLS, **0 anon-writable
policies**, 0 client reads broken, all 4 RPCs answering. `get_rls_audit()`
reports posture from the catalog, because RLS state cannot be read off HTTP
codes — a zero-row DELETE returns 200 whether RLS denied it or it matched
nothing.

### B119 · Disk IO budget — top consumer halved, cause was a double read
Supabase warned of IO budget depletion. `get_io_audit()` (new, read-only,
service-role only) named it: one shape at **694.69 GiB / 129,297 calls**,
five times the next.

Cause: `detect_steam` and `detect_rlm_and_limit` each paginated
`line_history` over the **same sport, same window, same ordering**, ~18 pages
apiece, and `main()` calls both per sport. 37 poller runs/day x 5 sports x 2
reads x 18 pages is about 6,660/day, against 129,297 measured — roughly 17
days. Closes.

Fixed by fetching the window once and sharing it: **18 requests per sport
where it was 36**, flags identical (21 steam, 1 rlm). Plus 160 MB of
genuinely duplicate indexes dropped (`20261021k`).

**Three wrong theories, recorded in-file so they are not retried:**
1. *Missing index* — wrong, `idx_line_history_sport_captured` already
   existed. I checked what the migrations created, not what the table has.
   `20261021j`'s index was redundant and `20261021m` drops it.
2. *Deep OFFSET* — wrong, keyset measured **0.7-0.9x (slower)**: ordering by
   `id` while filtering `(sport, captured_at)` cannot use that index for
   both. Reverted. Kept in `market_line._page`, where it measured 0.95x and
   fixes pagination that had **no ORDER BY at all**.
3. *Narrow the 6h window to 2h* — wrong, and would have cost product: **94
   steam flags became 48**. The 15-minute window is not the constraint,
   having two snapshots per key is, and `write_line_history` only records a
   line *change*, so a stable line is sparse by design.

**STILL OPEN:** 225 never-scanned indexes (84 MB) — assessable because
`stats_reset` is NULL so the counters are all-time, but each needs a check
for a nightly-only caller. And the client's Steam Room fires **30 parallel
`line_history` queries per visit** (`app/index.tsx:10058`).

### B120 · NHL/NBA props: the enrichment was being thrown away — **FIXED**
Both boards sat at 100% COVERAGE, conviction 0. `on_conflict` includes
`prop_line`, so a line move **inserts** a new row; the order is generate then
backfill then score, so every generator pass orphaned the L5/L10 enrichment.
L5/L10 was fed on 0 of 420 NHL and 0 of 3,222 NBA rows, while running the
backfill by hand fed 418/420 immediately.

- `prop_slot_reconcile.py` collapses a slot to its newest row (ungraded +
  COVERAGE only — 56,569 of NHL's 62,007 rows are graded history). Removed
  2,252 NBA rows, 49% of its live board. Wired into both workflows
  **between** generate and backfill.
- `nba_player_log.py` — NBA's ESPN endpoints return **404**, and that 404 was
  masking worse: the label fallback would have read **MIN as points**. Now
  1,756/2,188 NBA rows carry L10.

### B121 · 🚨 Every measurable NHL prop signal loses to its own price — **OPEN (product call)**
`calibrate_prop_signals.py`, 25,478 graded rows, inside the publishable
-300..+150 band:

| signal | hit | its b/e | n | edge |
|---|---|---|---|---|
| `nhl_prop_l10_extreme` | 59.9% | 60.4% | 3,511 | -0.5 |
| `nhl_prop_l5_cold` | 59.9% | 60.0% | 2,331 | -0.0 |
| `nhl_player_l10_extreme` | 58.9% | 61.0% | 2,142 | -2.0 |
| `nhl_prop_season_hit_pct_high` | 47.1% | 53.5% | 1,919 | -6.4 |
| `nhl_prop_l5_hot` | 46.2% | 53.3% | 769 | -7.1 |

**Zero VALIDATED.** The registry had been claiming 67-77% on these, because
it averaged over props nobody would publish (a `points_under` at -650) and
compared to a flat -110 bar. Three were running at **0.91-0.98 weight**.

Fixed at the mechanism: `signal_registry.baseline_pct` carries the measured
breakeven and the prop scorer passes it to `edge_weight(baseline=...)`, a
parameter that existed and no caller had ever used. Result on the same slate:
**PRIME 64 to 0, STRONG 4 to 0, LEAN 184 to 17.**

Also fixed: `PROP_TIER_THRESHOLDS` demands 4 signals / 3 classes for PRIME,
but two legacy rules (`l10>=8`, and `l5>=4 and l10>=8`) assigned tiers
**after** the gate and after the conviction demote. A sampled PRIME had 1
signal, 1 class, score 0.409 and conviction 67 — failed every criterion.
Dormant until the enrichment fix switched it on. One structural floor now
clamps after all promotions; regression-checked at **0 demotions on MLB (97
props) and NFL (173)**.

**THE OPEN QUESTION IS PRODUCT, NOT CODE:** NHL props have no demonstrable
edge to publish. Either keep NHL as the graded information board it already
is, or build genuinely new inputs (TOI, and the goalie/PK data that is
already collected but read off the prop row where the columns do not exist —
4 dead signals) and re-measure. NBA carries real depth (3-7 signals a row)
but its L5/L10 leans on last season until mid-November.

### Next, in order
1. **B5 — ship a build.** Still no `expo-updates`; a dozen-plus client fixes
   are queued behind it and it is the only true blocker.
2. **Calibrate NFL props** — 3,754 graded rows, a live surface, same method.
   NBA has 0 graded prop rows and cannot be calibrated yet.
3. **B119 residuals** — the 225 unused indexes and the Steam Room's 30-query
   client fan-out.
4. **B121 product call** on NHL props.

---

## B122 — the runner's clock decides what day it is, in ~70 files

**Opened 2026-10-21.** Andy: *"findout why ledger says 3-3 in recupeiits for
eveyr sport"*. It read 3-3 because `compute_surface_records._windows_today()`
was built from `dt.date.today()`, which on a UTC runner is **tomorrow's date
in ET** for the four-to-five hours a night the two disagree. Two crons fire
in that gap — `nfl_pipeline`'s `0 */6` at 00:00 UTC (8pm ET) and
`mlb_grade_overnight` at 04:10 — and whichever ran last owned the numbers.

`d7_start = today - 6` became 10-16 instead of 10-15, so every seven- and
thirty-day window on the Receipts tab dropped its oldest day:

| | stored | true |
|---|---|---|
| ledger d7 | 3-3 +1.10u | 4-4 +1.75u |
| ledger d30 | 25-19 +12.64u | 26-20 +12.04u |

Every surface shares that function, so this was not a Ledger bug. And the
worse case was still ahead: `mtd_start = today.replace(day=1)` would have
reset month-to-date to near zero at 8pm ET on **10-31**.

### Fixed
`compute_surface_records`, `grade_pick_snapshots`, `grade_orphaned_receipts`,
`refresh_card_grades`, `compute_external_source_records`,
`reconcile_receipt_results` — the files that write a record or a grade —
plus `sweat_dates.et_now()`, which used a fixed −4 offset and so returns the
**wrong ET day** for the 04:10 UTC run once EST begins **2026-11-01**. All
now zoneinfo with the constant as a no-tzdata fallback. Self-tested: six
helpers return `2026-10-21` while the runner sits on `2026-10-22 02:4x UTC`.

### Open
About **65 remaining non-scratch files** call `date.today()`. They are
audits, backfills, trainers and slate pickers, where a one-day shift is
cosmetic or self-correcting on the next run — but that is an assumption per
file, not a fact. `grade_pick_snapshots` is the warning: it *defined*
`_et_today()` and still had one window on the runner's clock, forty lines
below it.

`sweat_dates` exists so there is something to import, and **2 files import
it**. The sweep is mechanical — replace the call, confirm the module imports
`datetime`/`timezone`, run the entrypoint — but it is ~65 entrypoints and
belongs in a daylight session with a list, not at the end of a long one.

## B123 — POTD "no-pick" is written where a pick exists

**Opened 2026-10-21, parser fixed; the write path is the residue.**

Today's `Toronto Raptors -8.5` (a Win, TOR 135-CHI 92) was stored as
`no-pick` because the lean parser needs a market **word** and a basketball
spread has none. Fixed on shape, all eight lean formats regression-tested,
and a present-but-unparseable lean now leaves `result` NULL instead of
claiming no play.

What remains is upstream: **10-10 MLB** has
`daily_best_bet_history.lean = 'Chicago White Sox @ Cleveland Guardians'` —
the matchup written into the field where the side belongs. The pick text is
gone and cannot be recovered from the row, so that date is permanently
unresolvable. Worth finding what wrote it, because a POTD whose side is never
recorded cannot be graded by any parser, however good.

### Next, in order
1. **Apply `20261021q`** (prop signal field names — touches MLB, the live
   surface) and **`20261021r`** (ledger `sport_scope` labels).
2. **Ledger per-sport split** — a product call now that the premise is
   measured at 4.1% genuinely multi-sport. NHL 14-8 +11.83u is currently
   displayed as the global 114-152 on its own tab.
3. **B119 residuals** — 225 unused indexes, Steam Room's 30-query fan-out.
4. **B122 sweep** — the remaining ~65 `date.today()` call sites.
5. **UFC** — registry note, and a self-updating record if a record is shown.
