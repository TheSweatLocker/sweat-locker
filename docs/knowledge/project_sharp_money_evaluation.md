---
name: project-sharp-money-evaluation
description: "Action Network sharp money signal is supplementary, not a card anchor. 5/24 audit: 8-6 blind (57%), HEAVY signals only 50%, sample too small to commit prominence."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Sharp money picks (Action Network public/handle split) are a supplementary signal we display, NOT a primary justification for any pick on the public card.

**Why:** User explicit 5/27: "I fell like the model didnt love the Rays to begin with and we sent it based on the sharp data, that is a smalll factor we are evaluating to see if it is effective, not the priority." TB +29% sharp on 5/24 (the biggest signal of the day) lost to NYY 0-2 — we promoted it on the strength of sharp money alone and the model wasn't there.

**3-day audit verdict 5/24-5/26 (`_audit_sharp_money_3day.py`):**
- ML (n=40): 20-20 (50%) — flat across all magnitudes. HEAVY 50%, MEDIUM 56%, SMALL 42%. No predictive slope. Do not anchor card picks on ML sharp.
- TOTAL (n=24): 12-12 (50%) — HEAVY 5-2 (71%, n=7) promising; MEDIUM 0-5 (0%, n=5) is active FADE territory if it holds.
- SPREAD (n=16): 9-7 (56%) — HEAVY 4-1 (80%, n=5) promising but n too small.
- SMALL (1-5%) signals are noise across every market. Discard.

**How to apply:**
- Sharp data stays as displayed signal but never the lead justification for a card-anchor (POTD / DotD)
- If model edge is missing/weak, sharps alone are NOT enough to publish a play
- Need 5/25 + 5/26 AN data pasted to extend audit — currently only 5/24 has data on hand
- Long-term: build server-side AN ingestion so this audit auto-extends instead of relying on manual paste

Companion script: `_audit_sharp_money_3day.py` — multi-day breakdown with bucket-by-magnitude verdict. Related: [[feedback-source-gate-pattern]] [[project-spread-delta-trap-zone]].
