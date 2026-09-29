---
name: project-ncaaf-model-discussion-queue-902
description: 9/2 queued discussion topic — NCAAF model improvements + Strength of Record pulling/tracking + integration into model stack
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-02T18:58:59.260Z
---

🏈 **9/2 queued discussion topic** — NCAAF model improvement roadmap.
Currently NCAAF uses ensemble v2 (SP+ + returning production + roster
physicality + sharp-fade rules + MC simulator) but the model foundation
is still thin vs MLB's 6-lens stack.

**Why:** User wants to level up NCAAF modeling before Week 3 sample
solidifies. Strength of Record is one specific angle to pull + factor
in. Broader discussion covers other model dimensions we haven't
exhausted.

**How to apply:** When user starts this discussion, present:
1. Current NCAAF stack (what's live)
2. Strength of Record specifically (pull path + integration options)
3. Other candidate signals not yet built
4. Weight/priority ordering pre-launch vs post-launch

## Strength of Record (SoR) — user's specific ask 9/2

**What it is:** SoR quantifies how good a team's WINS have been (not just record). A 4-0 team that beat 4 top-25 opponents has a huge SoR; a 4-0 team that beat 4 FCS teams has a poor SoR. Better predictor than raw record because it strips schedule luck.

**Data source options:**
- **CFBD `/ratings/sor`** — endpoint exists, returns per-team SoR rating. Simplest pull.
- **ESPN CFP Strength of Record** — surfaced on their pages, requires scrape
- **Compute in-house** — sum of opponent SP+ / FEI × win indicator, roll our own

**Integration options into model:**
- Add as a `home_sor` / `away_sor` field on `ncaaf_game_context`
- Weight in ensemble alongside SP+ (currently the primary efficiency signal)
- Use as a signal in `ncaaf_signal_sources` (fires when |SoR gap| > threshold)
- Cohort dimension: "SoR-mismatch" games where market doesn't price the schedule strength gap

## Broader NCAAF model ideas (also candidates for discussion)

Beyond SoR, other CFB dimensions we haven't fully mined:
- **Coach spread record** — some HCs cover consistently, others fade
- **QB rushing designed carries** (mobile QB matchup vs opponent DL discipline)
- **Trap game detector** (top-10 team facing unranked opponent 1 week before rivalry)
- **Turnover luck regression** (teams w/ +6 turnover margin regress ~70% next month)
- **Transfer portal impact** (roster continuity beyond returning production %)
- **Home crowd noise proxy** (attendance vs capacity × win prob)
- **Kicking / red zone efficiency** (situational scoring)
- **Recruiting composite regression** (blue-chip ratio vs recent-year performance mismatch)
- **Weather × running game** (tuned per team's rush-lean tendency)

## 9/2 decisions (user)

**Timeline:** Defer SoR + FCS coverage to post-launch. Not blockers.
Ship all 9 signals **methodically** after launch, no rush pre-submit.

**Validation cadence (KEY DECISION — supersedes MLB 3-month graduation):**
CFB season is only 14 weeks. Waiting 3 months = losing 75% of season.
New cadence for CFB signals:
- Week 1: ship in shadow mode (compute + attach, no weight)
- After Week 1 grades (Sun/Mon): review hit rates + Wilson CI
- If clearing thresholds → weight at ~50% of full weight from Week 2
- Every 2 weeks: recompute, retune, ratchet up/down
- After 4 weeks: full validation OR drop signal entirely

Rationale: CFB Saturdays deliver ~50+ games per week — massive
sample-per-week. Different from MLB where 15 games/night take
weeks to accumulate. Match cadence to sample velocity.

**Signal build order (my rec, user agreed "methodically"):**
1. Trap game detector (rule-based, cheap)
2. SoR (passive first, then weighted per cadence above)
3. FCS chalk (fills visible UX gap)
4. Coach spread record (needs per-HC ATS history table)
5. Turnover luck regression (needs 30d rolling)
6. Kicking / red zone efficiency
7. Recruiting composite mismatch
8. Mobile QB rushing × opp DL discipline
9. Transfer portal + crowd noise + weather-rush team-specific

## Cross-references

- [[project-ncaaf-ready-809]] — current NCAAF stack (SP+ + returning production + 7 fade rules)
- [[project-nfl-ncaaf-week1-readiness-820]] — Week 1-3 self-calibration plan
- [[project-roster-physicality-823]] — OL/DL weight + class year signals live
- [[project-madden-top100-nfl-signal-824]] — NFL parallel (Madden ratings as roster-talent priors)
- [[project-vault-match-901]] — patterns for NCAAF also candidates for Vault Match catalog
