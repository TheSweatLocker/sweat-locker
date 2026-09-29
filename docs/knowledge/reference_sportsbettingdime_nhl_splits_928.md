---
name: sportsbettingdime-nhl-splits-are-scrapable-via-shadow-dom
description: Andy found a second NHL money-flow source. Data is in web-component shadow DOM — invisible to requests AND to page.content(); needs playwright + shadow walk. Verified 2026-09-28.
metadata: 
  node_type: memory
  type: reference
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-29T00:09:33.885Z
---

`https://www.sportsbettingdime.com/<league>/public-betting-trends/` carries bets% and money% per game. Leagues available: nfl, mlb, ncaafb, nba, nhl, ncaamb (from the `srwc-matchup-bar` `leagues` attribute).

**It is invisible to every normal approach.** Plain `requests` returns HTTP 200 and 406KB with **zero `<table>` elements and one occurrence of each team name**. Playwright's `page.content()` after a 9s wait returns the same — because the content lives in **shadow DOM** under custom elements (`srwc-public-betting-trends`), which `content()` does not serialize. There is no `__NEXT_DATA__`, and the API endpoint is not discoverable in the webpack chunks (checked 779/511/311/72 — the lazy chunks for this component — zero path-like strings).

**What works:** `page.evaluate()` with a recursive walk over `el.shadowRoot`. That yields ~16.5KB of text with 288 percent tokens in bets/money pairs (26%/45%, 85%/88%, 74%/55%).

**Team association** is NOT in the text — it comes from attributes on the same walk:
- `alt="Florida Panthers logo"` → full team name
- logo `src` ending `/nhl/FLA.png` → **the abbreviation directly**, which is the cleaner join key
- `aria-label="Bet Percent"` / `"Money Percent"` label the two metrics explicitly

Verified all five 2026-09-29 games present and in slate order (FLA/CAR, MTL/TOR, NYR/BOS, VAN/EDM, CHI/VGK).

**Why it matters:** NHL money flow is single-sourced (fadereport, plus ScoresAndOdds splits) against MLB's twelve. This would be a genuine second source, and it labels bets-vs-money explicitly, which is exactly the divergence the Money Flow card reads.

**Caveats before wiring:** playwright in CI is the fragile part — `nhl_pipeline.yml` already installs chromium with a `|| echo "⚠ chromium install failed"` fallback, so a failed install degrades to no data rather than a crash. And we have been here before: [[project_oddscrowd_client_render_921]] went JS-only and was effectively abandoned. A shadow-DOM scraper breaks on any component rewrite, so it needs a row-count assertion that fails loudly rather than writing zero rows quietly.

Probe scripts used: scratchpad `sbd_probe.py` / `sbd_teams.py` (shadow walk + attribute extraction).
