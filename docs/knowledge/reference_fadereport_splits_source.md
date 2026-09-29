---
name: reference_fadereport_splits_source
description: fadereport.com is a free full-splits data source across MLB/NCAAB/NHL/NFL — every game gets ML + Spread + Total splits via in-page toggle. Requires JS-rendering scraper (Playwright/Puppeteer) because tab content loads dynamically. WebFetch alone only sees default view.
metadata:
  type: reference
---

**fadereport.com** — free full-splits aggregator across MLB / NCAAB / NHL / NFL.

Discovered by user 2026-08-14 while working on NCAAB launch prep.

## What it actually is (corrected 2026-08-14 after tool-limitation confusion)

- **URL:** `/mlb`, `/ncaab`, `/nhl`, `/nfl` — one page per sport
- **Coverage per game:** **Full ML + Spread + Total splits for EVERY game** via in-page toggle
- **Access:** User sees toggle buttons on the site — click switches the market view for the whole slate
- **Free** — no paywall

## Tool caveat (important for scraping)

WebFetch alone gets ONLY the default market view. The toggle content is loaded via JavaScript client-side, so a static HTML fetch misses ~2/3 of the data. To scrape properly, use a **JS-rendering tool** (Playwright / Puppeteer / Selenium) to click through each market tab and capture the rendered content.

Ncaab_pipeline.yml already installs `playwright chromium` — same runtime works for fadereport scraper.

## What OddsCrowd is by contrast

- Full-splits firehose: all games × all markets always, no JS gymnastics required
- Data already in `oddscrowd_snapshot` JSONB column

## The line-movement test (2026-08-14) — findings still stand

Ran open-vs-close ML implied prob check on 3 games where OddsCrowd and fadereport ML data disagreed on sharp attribution:
- **StL @ Cubs**: line flat, neither confirmed
- **Bal @ TB**: fadereport direction confirmed (+0.2pp toward Rays)
- **SD @ CLE**: OddsCrowd direction confirmed (+0.9pp toward Cleveland)

Score 1-1 with one flat. Neither source is systematically correct on ML. Both may be measuring different books' data.

Bigger insight: **MLB lines barely move intraday**. Biggest movement of the day was +0.9pp implied. So "89% money on X" claims from any aggregator are measuring **book-specific ticket/handle patterns**, not aggregate market sharp truth — because real 89% money would move the line 3-5+ cents.

## Real sharp arbiter

Pinnacle line movement (sharp book by definition). Add to Odds API pull.

## Implementation path

1. **Nightly scraper with Playwright** — visit `/mlb`, click each market tab, capture splits per game per market
2. Write to `fadereport_signals` table (schema: game_date, sport, game_id, market, bets_home_pct, bets_away_pct, money_home_pct, money_away_pct, sharp_signal_pts, fade_tag)
3. Cross-reference against OddsCrowd `oddscrowd_snapshot` — both stay in the stack
4. 30d hit-rate audit on both sources' predictive value before weighting

## NCAAB angle (original discovery motivator)

Fadereport is a strong candidate for NCAAB splits since OddsCrowd may not cover D-I basketball. Now that we know it has full-market coverage per game, it can be primary NCAAB splits source. Test around Nov 3 launch.

## Layout observation

User noted the layout is cleaner than OddsCrowd. When we build the NCAAB slate UI (Nov 3), consider following fadereport's presentation pattern for splits display.

Related: [[project_sharp_money_fade_808]] tracks the sharp-fade decision. Phase 2A CAP-TO-LEAN based on OddsCrowd may need 30d audit to validate it's not firing on noise.
