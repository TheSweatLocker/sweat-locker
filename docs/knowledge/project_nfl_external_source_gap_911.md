---
name: project_nfl_external_source_gap_911
description: "NFL/NCAAF externals were silently dead: no Playwright in those workflows, Action Network networkidle timeout, and 13 stub fetchers across 4 sports. Fixed 09-25 except the stubs + oddscrowd client-render. watchdog_external_sources.py now catches it."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-25T04:42:29.661Z
---

# NFL external-picks coverage — the gap is silent parser failure, not config

Re-audited 2026-09-25 (was 2026-09-11, which found only 2 sources).
Coverage improved but the count is misleading. From `external_pull_log`
since 09-01, **9 sources are configured for NFL, 4 produce picks, 5 are
dark** — and every dark one logs `status='success'`.

| source | attempts | 0-pick runs | picks | last PICK | note |
|---|---|---|---|---|---|
| scoresandodds | 89 | 54 | 1213 | 09-24 | alive; 96-100 = **49%** |
| covers | 89 | 58 | 541 | 09-24 | alive |
| pickswise | 89 | 44 | 246 | 09-24 | alive |
| pickdawgz | 91 | 83 | 15 | 09-24 | technically alive, 15 picks = noise |
| oddscrowd | 89 | 61 | 408 | **09-20** | DARK — ReadTimeout + went JS-only |
| dimers | 89 | 83 | 90 | **09-17** | DARK — HTTP 200, 0 picks |
| action | 89 | 86 | 45 | **09-17** | DARK — some HTTP 500 |
| vsin | 89 | 89 | **0** | **NEVER** | HTTP 200, "success", all season |
| bettingpros | 89 | 89 | **0** | **NEVER** | HTTP 200, "success", all season |

oddscrowd going dark on 09-20 matches [[project_oddscrowd_client_render_921]]
exactly — it broke when that note said it would.

## THREE ROOT CAUSES — diagnosed and mostly fixed 2026-09-25

**1. No Playwright in nfl_pipeline.yml / ncaaf_pipeline.yml** (FIXED).
`dimers` and `action` render JS via `_playwright_helper.render_page`, which
returns `(None,'unavailable')` on ImportError; the fetchers treat that as a
graceful skip → 0 picks, HTTP 200, `status='success'`. `mlb_pipeline.yml`
installs playwright; these two never did. So both sources worked on the dev
machine and were dead in CI — proven, not inferred: dimers pulled 14 picks
on 14 games locally on the same slate that CI scored 0.
**Lesson: a per-sport workflow is its own dependency environment. A source
working in one sport proves nothing about another.**

**2. Action Network `wait_until='networkidle'` timed out at 45s every run**
(FIXED for NFL/NCAAF). The site holds long-lived connections so the network
never goes idle and `goto()` never returns. `domcontentloaded` + 8s settle:
0 → 13 picks in 9s. **Deliberately NOT changed for MLB**, which is producing
3,954 picks on networkidle — don't risk a live source to make four call sites
match.

**3. Thirteen literal stub fetchers** — `return [], 200` (NOT fixed; parsers
still to write). Found by an ast sweep, and they match the "never produced"
DB rows exactly: MLB cbs/oddsshark/scp/fangraphs/ballparkpal, NCAAF+NCAAB
covers/vsin/bettingpros, NFL vsin/bettingpros. MLB has working versions of
vsin + bettingpros to port from.

**oddscrowd** (NOT fixed): fetches 23 NFL game pages at HTTP 200 and extracts
0 picks — the site moved to client-render, same as its splits did per
[[project_oddscrowd_client_render_921]]. Needs routing through render_page.
Money flow itself is FINE: cleatz, fadereport and fadethepublic are all
current for NFL/NCAAF — that question is answered.

## The shared defect is a logging one, not scrapers

A pull that fetches HTTP 200 and extracts **zero** picks writes
`status='success'`. So vsin and bettingpros have run 89 times each,
returned 200, logged success, and delivered nothing for the entire
season. Nothing was ever going to alert. Same silent-failure class as
the 104 `|| echo` masks closed 09-24 — the defect announced itself as
green.

**Why:** external picks are a launch differentiator
([[project_external_transparency_differentiator]]). "We track how the
touts do" is weak if 5 of 9 NFL touts are untracked and no one knew.

**How to apply:** `watchdog_external_sources.py` now does this, wired into
all four pipelines as reporting-only (never `--strict`). Two calibration
attempts failed before the working one — don't redo them:

1. *Consecutive peer-relative misses* → alerted on HEALTHY weekly sources.
   Football touts publish on different days, so scoresandodds and covers
   each rack up miss runs of 8 while perfectly fine.
2. *Threshold = max(floor, source's own historical worst gap)* → scored
   dimers and action HEALTHY at an 8-day gap, because they had been broken
   for most of the window so their bad history became their baseline.
   **A source that is already down cannot supply its own definition of
   normal.**

What works: measure the GAP since the source last produced, counted only in
peer-productive days (days another source on that sport got picks), against a
flat floor of 5. A gap resets whenever the source posts, so it adapts to any
cadence for free. Clean separation on real data — healthy 0-2d, broken 8-17d.
Caveat: sports pulled only a few times a week accrue gap slowly, so use a 21d
window for football or a real break can sit under the floor.

## Also true of the NFL external sample

- Aggregate graded record **236-210 = 52.9%**, below the real 54.2%
  breakeven ([[project_nfl_prop_projection_no_edge_924]] has the same
  math). Not a back signal in aggregate.
- Highest-volume source (scoresandodds, 1213 picks) is **49%** — a coin
  flip. Volume is not quality; weight per-source, never pooled.
- Surfaces are ml / rl / total only. **Zero NFL prop externals.**
- Cite source count as "4 live of 9 configured", never "9".
