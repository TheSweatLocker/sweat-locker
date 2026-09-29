---
name: feedback_user_doubt_is_signal
description: When user signals doubt on a specific pick I recommended ("does that one carry a lot of variance", "still keep it?", "I was doubting it from the jump"), that doubt is high-information. Their gut catches matchup-specific and variance-specific signals the model doesn't fully weight.
metadata:
  type: feedback
---

When user pushes back on a specific pick I recommended — phrasing like "carries a lot of variance, doesn't it?", "still keep it?", "I'm not sure on that one", "thoughts?" — that doubt should be treated as a high-information signal, not a debate to win.

Pattern observed:
- 6/14 Wheeler BB Under +113: User asked "carries a lot of variance, doesn't it?" — I acknowledged variance, suggested Burns to ladder, but kept Wheeler on the card. Wheeler walked 2 in the 2nd inning. User said "I was doubting that one from the jump."
- 6/13 Multiple picks: User asked "you feel confident about these?" — I admitted only 1 of 5 was high confidence AFTER the post went out. Memory feedback_confidence_in_first_pass already covers this surface.

Why their doubt is informative:
- Founder context on matchup history (Skenes' dominance, Sánchez fade flag, Wheeler variance) that I don't fully encode
- Memory of past similar picks that lost in similar ways
- Real-time market reads (line moves, sharp action)
- Risk-tolerance calibration ("I can't afford another bad public night")

How to apply:
- When user expresses doubt on a specific pick, ask "what specifically?" before defending it
- Trust the gut signal — if THEY don't feel confident, the pick shouldn't be on the public card
- Easier to drop a pick they doubt than to walk back a loss
- "Lower-variance alternative" is the right framing — find a swap candidate proactively, don't defend the original
- Save the bull case for picks the user is already aligned on

Specifically NEVER do:
- Counter their doubt with statistics that overstate confidence
- Frame variance as "manageable" when they're calling it out
- Lock the pick after they expressed reservations without a clean swap

Related: [[feedback_confidence_in_first_pass]] [[feedback_let_engine_speak]]
