# NHL Historical Closing Odds Backfill Report — 2026-08-20

Run window: 2026-08-20T15:48:21.309319+00:00 → 2026-08-20T15:48:36.547943+00:00 (0.25 min)

## Verdict

Hockey-reference **DOES NOT** publish betting odds on NHL boxscore pages. Confirmed against multiple recent games (see probe evidence below). Full backfill was **skipped** — iterating 1,335 URLs would yield the same negative result. Alternative source required (see recommendations).

## Probe evidence

Sampled 5 recent nhl_game_results rows.

| # | Date | Away @ Home | Status | Odds found? | Betting keywords hit |
|---|------|-------------|--------|-------------|----------------------|
| 1 | 2025-04-17 | Calgary @ Los Angeles | 200 | no | — |
| 2 | 2025-04-17 | New York @ Columbus | 200 | no | — |
| 3 | 2025-04-17 | Washington @ Pittsburgh | 200 | no | — |
| 4 | 2025-04-17 | Tampa Bay @ New York | 200 | no | — |
| 5 | 2025-04-17 | Carolina @ Ottawa | 200 | no | — |

Probed URLs (full):
- https://www.hockey-reference.com/boxscores/202504170LAK.html
- https://www.hockey-reference.com/boxscores/202504170CBJ.html
- https://www.hockey-reference.com/boxscores/202504170PIT.html
- https://www.hockey-reference.com/boxscores/202504170NYR.html
- https://www.hockey-reference.com/boxscores/202504170OTT.html

## Full backfill

Skipped — no odds source available on hockey-reference.

## Alternative sources considered

| Source | Coverage | Access | Cost | Effort | Recommendation |
|--------|----------|--------|------|--------|----------------|
| **The Odds API** (historical endpoint) | 2020-present, all NA books | REST, API key | $ (paid tier for historical) | 2-3h script | Best for tight schema fit — same shape as our live nhl_game_context feed. Rate-limited but manageable for 1,335 games. |
| **OddsShark historical grid** | 2010-present, closing consensus | HTML scrape (per-team season pages) | Free | 3-4h — one page per team-season, ~90 pages | Free path. Consensus close only (no per-book). Layout stable but pagination is per team-year, and 2026-27 season not yet posted (fine — we're backfilling historicals). |
| **Sportsbook Review (SBR) archives** | 2015-present | HTML scrape (per-day league grid) | Free | 4-6h — many pages, brittle JS | Good coverage but heavier lift; JS-rendered odds table means requests-only scrape yields empty tables. Would need Playwright. |
| **Kaggle NHL datasets** (`martj42/nhl_game_data`, `emanuelcaralho/nhl-odds`) | 2005-2020 typical, sporadic 2020+ | CSV download | Free | 30 min — one CSV import | Fast one-shot for older seasons. Won't cover 2024-25 which is what we need. Useful for building a longer training set later. |
| **Action Network public spreadsheet exports** | 2020-present, closing consensus | HTML | Free | 2h | Comparable to OddsShark; layout has changed twice in 2 years — brittle. |
| **Odds API + oddsshark hybrid** | 2020-present | Mixed | $ + free | 4h | Belt-and-suspenders: Odds API for 2023+ where budget allows, OddsShark backfills 2020-22. |

## Recommended next step

1. **Primary path — The Odds API historical endpoint.**
   * `/v4/historical/sports/icehockey_nhl/odds` accepts a `date` param, returns
     the full snapshot of every book at that timestamp. Pass the closing snapshot
     (T ≈ commence_time - 5 min) per game and average books for `close_*` fields.
   * ~1,335 API calls × 2 seasons ≈ within the Startup tier's monthly credit budget
     if we're careful (one snapshot per game rather than sweeping the day).
   * Env var `ODDS_API_KEY` already exists in `.env`.
2. **Free-tier fallback — OddsShark scrape** if the paid endpoint is not funded before
   Oct 7. Ships consensus-close only, no per-book, but that matches how we use
   `close_home_ml` today (single number per side) so no downstream refactor needed.
3. **Punt option** — do nothing; let `signal_registry` self-heal from live 2026-27 fires
   (n hits n_min around Weeks 3-4). Fine if launch is LEAN-only; blocks any PRIME/STRONG
   chips until then.

This backfill script has left `_nhl_hockeyref_probe.jsonl` with per-URL probe evidence
(status codes, hit counts) so the follow-up script (e.g. `_nhl_odds_api_backfill_YYYY-MM-DD.py`)
can pick up the same `nhl_historical_closing_odds` destination table without any
migration work. `_nhl_hockeyref_output.jsonl` is created only if the probe finds odds
and the full backfill runs.
