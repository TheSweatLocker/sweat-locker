# BACKLOG — living

**Last verified: 2026-09-22**

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

### B6 · Doubleheader starters
Both Rays/Yankees legs carry the same two pitchers. Ordinal-pairing fix
is in `game_context.match_probable_pitcher` and verified correct against
live odds, but an in-flight pre-fix pipeline run overwrote the data.
Also unknown: whether the Games list collapses a DH to one card, which
would make leg 2 invisible.
VERIFY: query `mlb_game_context` for a DH date, compare
`away_pitcher`/`home_pitcher` across the two rows.

### B7 · `game_context.py` — 12 hardcoded percentages
Same class as the `play_of_day.py` set (fixed 034adc4b). These are
`audit_note` strings attached to picks. **Two are live decision
thresholds, not labels** — the panel/non-panel `hit_rate_note` and the
`-130/-149` juice band — so they need checking against live data before
being touched.
VERIFY: `grep -nE "['\"][^'\"]*[0-9]{1,3}(\.[0-9]+)?\s?%" mlb_pipeline/game_context.py | grep -v "^\s*#"`
NOTE: `generate_mlb_game_reads.py` shows 4 hits but all are comments
describing an already-fixed bug. Nothing to do there.

### B8 · Admin note — placement and intermittency
Two overlapping systems: `sport_registry` (`state_message`,
`today_note`, `tomorrow_note` — per sport, renders in-tab, this is the
one Andy wants) and `admin_notice` (app-wide gold banner, truncates).
Open: notes don't appear every time — trigger or client fetch is
unreliable, cause unknown. Wanted placement is below the
Time/Conviction/Best-Edge/Prime/Strong filters, above the game list.
MLB currently has a `tomorrow_note` but **no `today_note`**.

### B9 · UFC tab shows non-UFC promotions
Odds API has exactly one MMA key and returns `sport_title: "MMA"` for
every promotion — no field to filter on. Only authoritative source is
`ufc_upcoming_event.fight_card` (clean `fighter1`/`fighter2`), but it
holds **one event and is stale** (newest UFC 331, 09-19). Scraper must
keep future cards loaded before a name-match filter can work.

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
