---
name: project-sweat-badges-901
description: "9/1 Sweat Badges design — cross-sport game card chips (weather, sharp $, home div dog, get right spot, vault match, sport-uniques) + roadmap"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-02T02:49:10.013Z
---

🎯 9/1 game card badge system. Adds visible-depth chips to browse-view
cards so non-MLB sports (NFL/NCAAF/NBA/NCAAB/NHL) stop reading thin
to reviewers. All silent-hide when data missing; capped at 2 sweat
chips per card so tier + alignment + sweat stays under 4 mobile row.

**Why:** Reviewer safety pre-9/5 App Store submit + user directive
"data rich and ready for all sports before submit." MLB has tier +
alignment + NRFI + Padres badge; non-MLB were just tier + alignment.

**How to apply:** New badges land in `app/index.tsx` game card chip
render block (~line 13627). Order priority = weather > sharp > sport-
unique. Cap `_sweatBadges.slice(0, 2)`.

## Shipped Phase 1 (commit sweat_badges_v1_901)

- 🌧️ **Weather** — outdoor MLB/NFL/NCAAF. Temp≤32 OR wind≥15 OR precip≥40. Suppressed for dome/closed. Label picks severe dimension.
- 💰 **Sharp $** — universal. Fires when `splits_summary.triple_confirmed` has entries. Reads splits_v2_pipeline output.
- 🐕 **Home Div Dog** (NFL) — divisional home underdog. Uses `ctxAny.div_game` + `close_spread < 0` (nflverse convention).
- ⓘ **SP+ tooltip** — Explainer wrap on NCAAF SP+ label. Plain-english glossary help, no KenPom attribution.

## Roadmap (batches)

Phase 2 (needs server-side compute):
- 🎯 **Get Right Spot** — cross-sport bounce-back angle. Team's last game was blowout (≥10 NFL/NCAAF, ≥6 runs MLB, ≥12 NBA/NCAAB) OR 2+ game losing streak. Needs `team_recent_games` matview lookup → attach as ctx field.
- ⭐ **NCAAF Ranked** — both teams AP top-25. Needs AP rank pull (not yet in ctx).
- ⚾ **MLB Ace** — starter xERA ≤ 3.00. Check `home_pitcher.xera` / `away_pitcher.xera`.
- 😴 **NFL Rest edge** — days_rest gap ≥ 4. Already computed in ctx.
- 🌃 **NFL Prime-time** — SNF/MNF/TNF. Check game start time hour.
- ⚡ **NBA Pace clash** — |pace_gap| ≥ 5 possessions.
- 🥅 **NHL Goalie edge** — Elo gap ≥ 30 (from nhl_elo).
- 🚌 **NHL B2B fade** — team playing 2nd of back-to-back.

Phase 3 (moat play): [[project-vault-match-901]]
- 🎯 **Vault Match** — system-detected pattern badge. Proprietary dataset finds "external X record + model Y call = 74% n=23" scenarios (see [[project-per-source-tracker-moat-818]] + [[project-dissent-audit-822]]). New `sport_pattern_registry` table + recompute cron + context-attach + card render. Named "Vault Match" per user brand-cohesion preference (Locker/Vault vernacular).

## Design contract

- Use `<StatusChip variant="custom" color={THEME.X} icon="emoji" label="TEXT">`
- Label ≤ 12 chars so mobile row doesn't wrap
- Silent hide when required ctx field missing (never render placeholder)
- Universal chips first, sport-unique after
- Emoji + short label; no verbose copy
