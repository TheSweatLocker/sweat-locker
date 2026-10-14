# NCAAB Historical Closing-Odds Backfill — 2026-08-20

Status: **HALTED at precondition #1 — session-auth creds missing from `.env`.**
Zero page fetches attempted (per guardrail). Scaffolding is fully staged;
the moment the operator adds two lines to `mlb_pipeline/.env`, a single
re-run auto-verifies layout and (if it passes) backfills every team page
for the requested seasons.

Sister to the UFC BFO backfill (`_ufc_bfo_backfill_2026-08-19.py`) —
same JSONL-authoritative / DB-best-effort / resumable pattern.

---

## What was verified this session (probe-only, no scraping)

### 1. Efficiency data source's paid API has no odds/games endpoint

Probed the operator's paid API (`KENPOM_KEY` Bearer auth) against every
plausible endpoint name:

| endpoint       | status | notes                          |
|----------------|-------:|--------------------------------|
| `ratings`      | 200    | already used by production     |
| `four-factors` | 200    | already used by production     |
| `fanmatch`     | 200    | requires `d=YYYY-MM-DD`; returns 146 games/day but **only KenPom's own predictions** (HomePred / VisitorPred / HomeWP / PredTempo / ThrillScore) — **no Vegas line, total, or ML** |
| `schedule`     | 404    | not exposed                    |
| `games`        | 404    | not exposed                    |
| `history`      | 404    | not exposed                    |
| `odds`         | 404    | not exposed                    |
| `vegas`        | 404    | not exposed                    |
| `lines`        | 404    | not exposed                    |
| `spreads`      | 404    | not exposed                    |
| `games-log`    | 404    | not exposed                    |
| `teamgames`    | 404    | not exposed                    |
| `teamlog`      | 404    | not exposed                    |
| `box`          | 404    | not exposed                    |

Conclusion: the paid API doesn't ship historical closing odds under any
current endpoint. The only route through this vendor is the browser
pages (`team.php`, `gameplan.php`, etc.).

### 2. `team.php` requires session-cookie login

Confirmed all of these return `302 -> register-kenpom.php` without a
logged-in session cookie:

- No auth header at all
- `Authorization: Bearer <KENPOM_KEY>` (API key isn't a session token)
- `Cookie: kenpomid=<KENPOM_KEY>` (and 3 other guessed cookie names)
- `?key=<KENPOM_KEY>` query param

The site's login handler at `https://kenpom.com/handlers/login_handler.php`
IS live (GET 200, POST returns `Set-Cookie: PHPSESSID=...`), so a normal
POST with valid email + password successfully establishes a browsable
session. That's the scaffolding path baked into the script's `login()`.

### 3. `.env` audit

`mlb_pipeline/.env` contains: `SUPABASE_URL`, `SUPABASE_KEY`,
`SUPABASE_ANON_KEY`, **`KENPOM_KEY`** — but **not** `KENPOM_EMAIL` or
`KENPOM_PASSWORD`. Per the task guardrail, this is a hard halt.

---

## What the operator needs to do to unblock

1. **Apply the migration** in the Supabase SQL editor:
   `supabase/migrations/20260820_ncaab_historical_closing_odds.sql`.
   Creates `ncaab_historical_closing_odds` (id, game_date, away_team,
   home_team, kenpom_game_id, season, close_home_ml, close_away_ml,
   close_spread, close_total, source, source_url, raw_payload,
   fetched_at) with UNIQUE(game_date, away_team, home_team) +
   `NOTIFY pgrst, 'reload schema'` footer.

2. **Add two lines to `mlb_pipeline/.env`:**
   ```
   KENPOM_EMAIL=<operator email>
   KENPOM_PASSWORD=<operator password>
   ```

3. **Verify-only smoke run** — samples Duke / Kansas / Houston, confirms
   odds columns are present in team.php this season, then stops. No
   full backfill risk:
   ```
   cd mlb_pipeline
   python _ncaab_kenpom_odds_backfill_2026-08-20.py --season 2025 --verify-only
   ```
   Read the auto-generated `_ncaab_odds_backfill_report_2026-08-20.md`.
   The "Verification samples" section will show the exact header cells
   the parser saw, and which columns it mapped to Line / O/U / ML.

4. **Full backfill:**
   ```
   # single season smoke (about 2.5s × 365 = ~15 min):
   python _ncaab_kenpom_odds_backfill_2026-08-20.py --season 2025

   # all 15 seasons 2010-11 -> 2024-25 (~4 hours):
   python _ncaab_kenpom_odds_backfill_2026-08-20.py --seasons all
   ```
   Resumable via JSONL — killable + restartable without losing progress.

5. Once populated, re-run:
   - `python ncaab_cohort_backfill.py` — spread/total cohorts pick up
     the LEFT JOIN'd close lines automatically.
   - The 45-signal NCAAB registry validator (from
     `_ncaab_ml_signal_backfill_2026-08-19.py`) — the ~30 signals that
     depend on `close_spread` / `close_total` now have sample.

---

## Alternatives if the operator doesn't want site creds in `.env`

| Path                          | Cost           | Coverage         | Effort | Notes                                                                 |
|-------------------------------|----------------|------------------|--------|-----------------------------------------------------------------------|
| The Odds API historical       | ~$60-150/mo    | ~95% back 2-3 yr | 2-3 d  | `ODDS_API_KEY` already present. Add `odds-history` endpoint pull; reuse this script's JSONL + DB write path. |
| Just wait for real-time       | $0             | 100% forward     | 0      | `ncaab_odds_pull.py` already wired. By Feb 15 ~2000 games banked.     |
| Kaggle CBB datasets           | $0             | Loose            | 1-2 wk | Coverage inconsistent; team-name normalization brutal; not recommended. |

The **wait-for-real-time** path is the readiness report's "Scenario B"
default. Nothing breaks if the operator prefers to skip the historical
backfill entirely — spread/total signals ship UNVALIDATED (w=0.30 floor)
and auto-promote as `ncaab_odds_pull.py` captures real games.

---

## Design notes baked into the script (for reviewers)

### Verify-before-backfill (two-gate)

1. **Precondition 1 — creds present.** Halts immediately if
   `KENPOM_EMAIL` / `KENPOM_PASSWORD` missing. No fetch attempted.
2. **Precondition 2 — odds columns exist.** After login, samples 3
   marquee teams (Duke / Kansas / Houston) and only proceeds to full
   iteration if at least one sample surfaces ≥5 parseable odds rows.
   `--verify-only` short-circuits at this gate so the operator can
   inspect the diagnostics without triggering ~4 hours of scraping.

### Iowa State / Iowa St. alias collision (from readiness report)

The readiness report flagged that the 2025-26 rating snapshot has
"Iowa State" and "Iowa St." as separate rows in `ncaab_team_aliases`.
`team.php` renders the "St." variant, so the loader's `_remap_state_variant`
helper detects the collision and remaps the "State" spelling to the
"St." canonical in the in-memory lookup. This is done for 25+ common
`Foo State` / `Foo St.` pairs and logs each remap to stdout so gaps
surface immediately.

The full permanent fix still requires a follow-up `ncaab_enrich_aliases.py`
run against the 25-26 snapshot (called out separately in the readiness
report). This script's remap is defensive so backfill isn't blocked
waiting on that follow-up.

### Dedupe + merge

Each game appears on both teams' `team.php` pages. In-memory
`seen_this_run[(game_date, away, home)]` merges the two parses:
first-parse wins for populated fields, second parse fills any NULLs
(useful when only one team's page listed the ML column).

### Sign convention

Matches `ncaab_odds_pull.py` + `ncaab_game_context.py`:
`close_spread` is HOME perspective, NEGATIVE = home favored. When
parsing an away-team's page (where the Line column is that team's
perspective), the script flips sign before dedupe key assembly.

### Naming rule enforcement

Per `feedback_no_kenpom_attribution.md`, no user-facing surface may
name a source system. The DB column `source = 'kenpom_team_php'` is
internal-only (audit + provenance); the module docstring and comments
neutralize to "efficiency data source" wherever the vendor name would
have leaked into a public artifact.

### Rate limiting

Global `_LAST_GET` timestamp enforces `TEAM_DELAY_SEC = 2.5` between
consecutive team fetches — courteous for a single-operator subscription
and well within reasonable scraping etiquette.

---

## Files shipped this session

- `supabase/migrations/20260820_ncaab_historical_closing_odds.sql` —
  landing table + indexes + PGRST reload notify
- `mlb_pipeline/_ncaab_kenpom_odds_backfill_2026-08-20.py` — the
  scaffold (gitignored per `_*.py` convention; `git add -f` if kept)
- `mlb_pipeline/_ncaab_odds_backfill_report_2026-08-20.md` — this
  report (**note:** the script overwrites this file on each run so
  it always reflects the latest run's coverage; the pre-run version
  above is preserved in the git commit if committed as-is)

## Estimated runtime (once creds are in)

| Scope                          | Team fetches | Approx wall time |
|--------------------------------|-------------:|-----------------:|
| `--season 2025 --verify-only`  |            3 |           ~10 s  |
| `--season 2025`                |         ~365 |           ~15 min |
| `--seasons 2020-2025`          |        ~2200 |           ~90 min |
| `--seasons all` (15 seasons)   |        ~5500 |           ~230 min |
