# Sweat Locker Language Guardrails

**Purpose**: this repo contains a lot of user-facing copy — in-app notes,
Jerry prose prompts, empty states, error messages, marketing hero copy.
Wrong language positions Sweat Locker as a wagering / sportsbook advisor
service. Right language positions it as **analytics + personal bet tracking**.

That distinction matters legally, for App Store compliance, and for user
trust. Every copy contributor reads this before writing.

---

## The positioning statement (memorize)

> **Sweat Locker is a sports analytics platform. Users see our models'
> analysis and use it to inform their own bets, placed at their own
> sportsbook of choice. We do not manage bets, grade bets, or take positions.**

Every piece of copy needs to be readable with this framing.

---

## Language you MUST avoid

| ❌ Avoid | ✅ Use instead |
|---------|--------------|
| "your pick" | "the analysis you looked at", "the pick our model surfaced" |
| "your bet" | (don't reference user bets at all — we don't know them) |
| "your track record" | "our model's track record", "our published analysis performance" |
| "we'll regrade your pick" | "the analysis has been updated" |
| "we caught an issue affecting your picks" | "we identified an issue in our published analysis" |
| "bet the under" | "the analysis suggests under" |
| "hammer this play" | "high-conviction analysis" |
| "lock" (as a bet type) | "PRIME tier", "highest conviction" |
| "guaranteed winner" | (never) |
| "we recommend betting X" | "our models point to X" |
| "your action tonight" | "tonight's high-conviction analysis" |
| "you should take Cubs -1.5" | "Cubs -1.5 clears our PRIME threshold" |

---

## Voice attributes

**Data-first**: cite numbers before adjectives.
- ❌ "This is a great play"
- ✅ "This bucket hits 63% at n=45"

**Own limitations**: variance and losing streaks are stated as fact.
- ❌ "We had a bad night"
- ✅ "Our 7-day hit rate is 44% (n=18) vs 30-day baseline 58%"

**No apologies, no defensiveness**: analytics observation, not customer service.
- ❌ "Sorry about the tough stretch"
- ✅ "Recent variance below the 30d baseline — 90d holds at X%"

**Non-directive about user action**: we describe what the models see; users decide.
- ❌ "You should take this"
- ✅ "Our models see edge here"
- ❌ "Skip this game"
- ✅ "No high-conviction read on this game"

---

## Templates for common surfaces

### Empty state (sport not in season)
```
NCAAB returns November 3.
Our efficiency model + Panel ensemble refresh in the week before season tips.
```

### Empty state (no picks today)
```
Slow slate — 2 qualified plays.
Sitting on hands is a valid analytical stance.
We don't pad the card with LEAN-tier when the models don't stack.
```

### Losing streak note (in-app card)
```
7-day model hit rate: 44% (n=18)
30-day baseline: 58%
90-day: 61%

Variance below the recent average. No methodology change.
Reset conditions: next 15 graded picks vs 30-day baseline.
```

### Model change announcement
```
Shipped: NFL V4 XGBoost lens
What it does: trained on 2020-2024 nflverse; adds a data-driven
              signal alongside MC, V3, Panel, Matchup-EPA.
Backtest: MAE 12.4 pts on held-out season.
Effect: NFL primary_play now requires 4+ of 5 lens agreement for PRIME tier.
```

### New-user first-week note
```
Your first 7 days: 3-4 record on the picks you followed.
That's a 7-pick sample. Our 90-day is 58%.
Fair evaluation window is 30+ picks.
```

---

## Legal-adjacent phrasing rules

**Never claim guaranteed outcomes.**

**Never suggest fund transfer, deposits, or bet placement.**

**Never reference specific dollar amounts of user bets or winnings** (we
don't know them; guessing risks impersonating a bet-tracking service).

**Do reference historical hit rates and cohort samples.** Those are our
model's performance metrics, not user financial performance.

**Do reference "the analysis" or "the pick" as an object we published.**
"The Cubs ML PRIME pick landed at 65% MC win probability" is analytics.
"Your Cubs ML pick" isn't.

---

## Prompt engineering for Jerry (LLM synthesizer)

Jerry has a natural pull toward tout-language ("this is a hammer", "you
gotta take this"). Prompt guardrails enforce our voice:

```
STYLE RULES for Jerry synthesis prompts:
  * Analytical narrator, not tout
  * Numbers before adjectives: "L3 ERA 2.45" before "dominant form"
  * Never use "lock", "guaranteed", "hammer", "bet this"
  * Reference our own historical hit rate when available, phrased as
    "our published analysis at this tier hits X%"
  * When the analysis suggests skipping: "no high-conviction read" —
    never "avoid" or "fade" as a directive
  * Never address the user in second person ("you should…") — this
    creates the sportsbook-advisor framing we're avoiding
```

Any Jerry prompt template edited going forward must preserve or
strengthen these rules.

---

## When you're not sure

Ask yourself: **"If a state gambling regulator read this line, would
they call us a bet advisor or an analytics platform?"**

If bet advisor → rewrite.
If analytics platform → ship.

If unclear → default to the more analytical phrasing. The cost of being
too data-focused is boring copy. The cost of being too tout-y is a
cease-and-desist.
