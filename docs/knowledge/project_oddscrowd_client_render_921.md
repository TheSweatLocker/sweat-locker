---
name: project_oddscrowd_client_render_921
description: "OddsCrowd moved money%/bets% to client-side rendering ~2026-09-21 — plain HTTP scraping can no longer see it. Hang fixed separately. fadereport still covers money flow, so not a blackout. Decision pending."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-21T21:26:13.443Z
---

Found 2026-09-21 after the slate-adjusted watchdog
([[project_card_lineage_calibration_921]] era, commit 31ebbf21) stopped
hiding it behind false alarms.

## What broke

OddsCrowd now renders the odds/splits table client-side. The served HTML
carries only the skeleton:

    Moneyline / Moneyline / Blue Jays / Orioles / Spread / Spread / ...

No `Bets` / `Money` percentage rows. Five `%` tokens on the whole page,
all in ad copy. `externals_oddscrowd.py` regex expects
`Moneyline\nMoneyline\n<a>\n<b>\nBets\n##%\n##%\nMoney\n##%\n##%` and
matches nothing, so it returns 0 picks cleanly.

Ruled out:
  * **Not bot detection** — full browser headers (UA, Accept,
    Accept-Language, Referer, Sec-Fetch-*) return byte-identical output.
  * **Not embedded JSON** — no `__NEXT_DATA__`, no `self.__next_f`, zero
    JSON keys matching percent/pct/bets/money/handle.
  * **API not guessable** — it is a Nuxt app against `api.oddscrowd.com`;
    8 obvious routes for game 5506843 all return `{"error":""}` 404. The
    two `/​_nuxt/*.js` chunks in the HTML are entry chunks with no API
    strings; the data route lives in a dynamically-loaded bundle.

Last good pull: **2026-09-20T22:02Z**. Break is ~1 day old.

## Why it is not an emergency

`fadereport_signals` is alive and independent — 149 rows on 2026-09-21,
carrying `money_side_pct` / `bets_side_pct` / `sharp_side_norm` /
`strength_tier`, i.e. the same money-vs-bets divergence.

`public_splits_archive` already merges both sources side by side
(`oc_money_pct`/`oc_bets_pct`/`oc_divergence` vs
`fr_handle_pct`/`fr_bettors_pct`), and fadereport has been the larger
feed for a while:

    capture day   rows   oddscrowd   fadereport
    2026-09-21     410         111          332
    2026-09-20    1988        1164         1132
    2026-09-19    3838        1482         2868
    2026-09-18    4775         227         4601
    2026-09-17    7120         750         6472

So money flow degrades but does not go dark. The real cost is
**corroboration**: [[feedback_sharp_money_discipline_802]] wants 2+
contrarian sources before a FADE, and with oddscrowd gone that rule can
only ever see one. Expect fewer FADEs, not wrong ones.

## Options for Andy (undecided as of 2026-09-21)

  a. **Reverse-engineer the API** — walk the Nuxt build manifest to find
     the data route. Moderate effort, and it can break again on their
     next deploy.
  b. **Headless browser (Playwright)** — works, but heavy in CI, fragile,
     and much more load on their site.
  c. **Lean on fadereport, drop oddscrowd** — zero work, loses the
     two-source corroboration gate. Would want the FADE rule relaxed to
     1 strong source, or a third splits source added.

Recommendation leaned (c) + find a third source, because (a)/(b) buy a
dependency that already proved brittle.

## Fixed regardless (commit 8fa33381)

The same investigation found and fixed a genuine hang: the probe loop
enumerated every integer between the lowest and highest game ID on the
list page (387 requests at 20s timeout), so the pull ran **13.7 minutes**
and exited 0. Now edge-bands only + wall-clock budget + early exit:
**820s -> 65s**. Also killed a bare `except: continue` and made a source
that writes nothing on a non-empty slate print ⚠ instead of ✓ —
"success" in pull_externals_mlb only ever meant "raised no exception".
