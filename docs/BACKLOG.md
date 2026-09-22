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

### B23 · Situational rollups frozen since 09-16 (ALL sports)
Andy spotted it as "SEA and NE just have 1-0" on the NFL games tab. It
is not SEA and NE and it is not NFL — **all 32 teams** showed exactly
one game, and the same freeze hit every sport.

`nfl_game_results` is correct: 32 scored games, 2 per team. The badge
reads `team_situational_records`, which migration `20260916a` turned
from a matview into a filtering VIEW over a renamed
`team_situational_records_full`. The refresh functions still named the
old object, so they have returned `42809: not a table or materialized
view` on every call since 09-16. The 12 workflow steps that call them
used `curl -s` with no status check under `continue-on-error`, so a 400
and a 204 were the same event. Six days, six pipelines, silent.

Fixed: `76489d10`.. — refresh fns repointed at `_full`
(`supabase/migrations/20260922a_*.sql`), and all 12 steps now capture
the HTTP code and emit `::error::` with the body.

**OPEN — needs Andy: the migration must be applied.** No migration
runner exists in the repo; until `20260922a` is run in Supabase the
refresh RPCs keep failing and the badge keeps showing one game.
VERIFY (after applying):
`select public.refresh_team_situational_records();` then
`select team,wins,losses,pushes from team_situational_records
 where sport='NFL' and market='spread' and filter='overall';`
— expect 2 games for all 32 teams.

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
- **Pure UFC.** Backend filter shipped (`fd1a6d3d`, `29aa26d9`); the
  client still renders every promotion because `mma_mixed_martial_arts`
  is one Odds API key with `sport_title: "MMA"` for all of them. B9.
- **Process + data.** `sherdog` and `mmajunkie` externals are `return []`
  stubs; `bfo` matches 0 picks. B19.
- **Model performance.** Never audited end to end. Needs a graded record
  by tier before anything ships.
VERIFY: see B9/B19; model record needs a direct query against graded UFC
reads.

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
