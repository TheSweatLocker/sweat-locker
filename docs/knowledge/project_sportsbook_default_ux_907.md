---
name: project-sportsbook-default-ux-907
description: "🎯 9/7 queued: Hard Rock Bet as default book — legal/BizDev risk of prominent placement + UX for DK/FD/BetMGM users who don't use Hard Rock"
metadata:
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T20:22:51.908Z
---

**Queued 9/7 evening.** User: *"Queue discussion of hard rock bet odds being the primary or if we are getting in hot water by having the name on there so out there? Or what about people who do not use hard rock and use DK, thoughts?"*

## The two questions

**Q1 — Legal/BizDev risk:** is featuring "Hard Rock Bet" prominently in the UI a problem?
**Q2 — UX for non-HR users:** most bettors use DK/FanDuel/BetMGM — what do they see today, and is that a bad experience?

## Where "Hard Rock Bet" appears today

- Game Detail → `YourBookTiles` section titled `"Your Book · Hard Rock Bet"`
- Parlay legs default to Hard Rock's odds when displayed
- No user setting to choose a different book
- Data pipeline pulls odds from ALL major books (BOOKMAKER_MAP: DK, FD, ESPN Bet, BetMGM, Caesars, Bet365, Hard Rock, Williamhill_us) — Hard Rock is just the DEFAULT display, not the only source

## Q1 — Legal / BizDev thinking

**Low-risk (probably fine):**
- Displaying public odds with attribution isn't trademark infringement — odds are quotable market data. Every odds-comparison site does this without a partnership.
- Trademark usage: "Hard Rock Bet" as a factual reference to the book (nominative fair use) is generally allowed. Style guide should ensure not implying endorsement.

**Real risks to actually think about:**
- **Undisclosed affiliation implication.** If the UI says "Your Book · Hard Rock Bet" but Hard Rock isn't a partner, some users may assume we have a paid relationship. FTC endorsement rules require disclosure of material connections. If no affiliation exists → clarify "informational odds display, no relationship" somewhere.
- **State gambling commission scrutiny.** A handful of states (MA, NJ, PA) have taken interest in third-party sports apps that appear to steer bettors. Prominently defaulting to one book without transparency could invite that scrutiny — even without deep-link revenue.
- **Deep-link / affiliate flow.** If we ever add "Place bet at Hard Rock" buttons, that becomes an actual affiliate relationship requiring: (a) partnership agreement, (b) FTC disclosure, (c) state-level licensing check.
- **Data licensing.** Some feeds require attribution. Verify what our odds provider (probably Odds API) requires — likely just a footer credit, not a per-book default.

**Not a lawyer** — worth a 30-min call with a gambling-industry attorney before public launch.

## Q2 — UX for non-HR users

**Reality check on US market share:**
- DraftKings + FanDuel: ~70% combined
- BetMGM: ~10%
- Caesars: ~7%
- Hard Rock Bet: <5% (regional strength in FL, growing but small nationally)

**Which means: 90%+ of our users don't use Hard Rock.** They see the odds and can't cross-reference to their own book's line without leaving the app.

**Options to discuss (pros/cons):**

| Option | Effort | UX win | Legal cleanup |
|--------|-------:|--------|--------------|
| A. User-picks-default book in Settings | Medium (1-2d) | Big — everyone sees their book's odds | Removes single-book default, less "endorsement" appearance |
| B. Show best-available odds (odds shopper mode) | Medium (1-2d) | Best UX for sharp bettors ("+120 at DK · +115 at MGM · best DK") | Neutral — no book featured |
| C. Show multiple books' odds inline | Small (0.5d) | Useful but visually noisy | Neutral |
| D. Rebrand to "Sportsbook Odds" (generic) + let user tap for details | Small (0.5d) | Loses the Hard Rock prominence | Neutral |
| E. Keep Hard Rock but add clear disclaimer | Small (0.5d) | Doesn't fix the DK-user pain | Cleans up any endorsement implication |

**My gut:** **A + B combined post-launch.** Ship v1 with a Settings toggle for default book (A) — solves the 90% pain. In v1.1 add a "compare" view showing best-available across all books (B) — becomes a differentiator for sharp bettors. Add disclaimer text now (E) as a launch-week cleanup regardless.

## 9/7 update — DECISIONS + DISCLOSURE SHIPPED

**User confirmed:** no affiliate agreement with Hard Rock or any sportsbook. Plan: **A (settings toggle) + B (best-available)** for post-launch. Disclosure needed NOW for App Store submission.

**Shipped 3b405805:**
- Bottom disclaimer strip: added "Not affiliated with any sportsbook"
- Game Detail: renamed "Your Book · Hard Rock Bet" → "Sportsbook Odds" + hint noting no affiliation
- Settings → new "🏈 Sportsbook Disclosure" row with full plain-English "no partnership, no revenue share, no affiliate, no deep-link monetization with any operator" statement — reviewer-findable

**Still to do (post-launch):**
- v1.0.1 = Option A (Settings toggle for default book, DK/FD/BetMGM/Caesars/HR/etc.)
- v1.1 = Option B (best-available odds compare view — sharp bettor differentiator)
- Consider: ToS website copy (thesweatlocker.com) may need matching disclosure clause added

**Data plumbing:** already there. `BOOKMAKER_MAP` + game.bookmakers[] carry all 6+ books per game. Just need the UI selector + a default-book state persisted in AsyncStorage.

## Questions to resolve in the discussion

1. Do we have a Hard Rock affiliate agreement? If yes, contract may require prominence (constrains options).
2. What do we want the app's stance to be — book-agnostic (Action Network) or opinionated-default (Underdog-style)?
3. Post-launch v1.0.1 candidate or v1.1?

## Related
- [[project_launch_day_907_priorities]] — launch is imminent, so legal review shouldn't block v1 IF we can add a disclaimer now
- [[project_launch_backlog_729]]
- [[reference_data_source_strategy]] — per-sport data source matrix
