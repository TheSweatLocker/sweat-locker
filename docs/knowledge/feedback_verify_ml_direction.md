---
name: Always verify ML/spread direction before framing — don't infer from spread_delta alone
description: Sloppy framing on 5/7 Marlins ML cost public credibility; verify all three (ML odds + close_spread + projected_spread) together before claiming favorite/dog/plus-money status
type: feedback
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
When framing a pick, NEVER infer favorite/underdog/plus-money status from `spread_delta` sign alone. Always verify the actual market direction by pulling all three signals together.

**Why:** On 5/7 I framed Marlins ML as "+1.5 home dog getting plus money" when actually they were -126 ML favorite at -1.5 RL. Andy posted the social card based on that framing before catching the error. The pick itself was right — Miami ML at -126 is still the model's PRIME confluence call — but the framing was wrong, made the brand look sloppy, and showed up live to viewers. He flagged it bluntly: "do better."

**Root cause:** I read `spread_delta = +2.45` and `open_spread = 1.5` (positive) and inferred Miami was the underdog. The data convention is more nuanced — `open_spread` is stored as the away team's spread in our system, so home Miami at +1.5-equivalent meant -1.5 from their perspective. I didn't pull `home_ml_open` (-126) which would have made the favorite/underdog status unambiguous.

**How to apply:** Before framing any ML/spread pick (in social copy, best-bet writeups, or recommendations), explicitly query and reason from:
1. `home_ml_open` / `away_ml_open` (or close) — definitive favorite/underdog determination from negative ML
2. `close_spread` (signed, home team perspective) — confirms run-line favorite
3. `projected_spread` (signed, home team perspective) — model's direction

The "plus money" claim specifically requires odds > +100, not just spread inference. Calling -126 plus money is wrong.

When in doubt, dump the raw odds row before writing copy. Treat directional framing as a load-bearing claim that warrants verification, not a quick read.

**Recurrence (2026-05-20):** I called Athletics the "Dawg of the Day" because v4 spread was -4.6 (model favors ATH big) and the RL was plus money — but ATH was a -126 ROAD FAVORITE, not a dog. Plus-money RL on a favorite is a value play, not a "dawg" framing. Same error pattern: I read the model direction + the RL juice and skipped the actual ML. **A RL at plus money on a favorite is RL value, not a Dawg pick.** The Dawg slot is reserved for actual underdogs (ML > 0), and is selected server-side via `generate_dawg_of_day.py` — never improvise it in social copy. Today's actual app DotD: Milwaukee Brewers +100 vs Chicago Cubs -120.
