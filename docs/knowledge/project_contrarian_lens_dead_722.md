---
name: contrarian-lens-dead-722
description: "60d audit: contrarian value picks are NOISE. Panel-alone disagreement with v3+jerry hits 39.4%. Panel-vs-market dog contrarian 48-50%. Do NOT surface contrarian picks."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-22T21:38:17.973Z
---

**Set 2026-07-22 after queue #4 audit.**

## Question audited
"Are contrarian value picks (single lens or MC disagreeing with market/rest of models) a real edge?"

Triggered by MIN ML +114 pattern on 7/22 slate (MC said 59% MIN win vs
market implied 43%, layer disagreement — I flagged as "contrarian
value" candidate but caveated).

## Data
- 60d graded games with panel_implied_margin populated
- n = 281 games total in window
- n = 228 with panel + home_win non-null

## Results

**Baseline (panel alone as sides lens):** 136-92 = 59.6% on 228 games.
Panel = solid lens, matches 7/21 audit's 72% and 60d prior findings.

**Contrarian buckets (panel disagrees with crowd):**

| Bucket | W-L | Pct | n |
|---|---|---|---|
| Panel vs v3+jerry (both opposite) | 13-20 | **39.4%** | 33 |
| Panel + v4 vs jerry + v3 | 6-9 | 40.0% | 15 |
| Panel ALONE (v3, v4, jerry all opposite) | 6-7 | 46.2% | 13 |
| Panel-market dog (panel loves home dog) | 5-6 | 45.5% | 11 |
| Panel-market dog (panel loves away dog) | 12-13 | 48.0% | 25 |

**All contrarian buckets are BELOW break-even.** Panel dropping from 59.6%
baseline to 39.4% when alone means: **panel's value comes from consensus
signal, not standalone disagreement.**

## Rule going forward

**Do NOT surface contrarian value picks.** MC-vs-market ≥15pt AWAY value
plays like MIN ML +114 that came out of the 7/22 audit are NOT actionable
signal — the 60d audit shows contrarian disagreement is a fade signal, not
an edge signal.

**Panel is a CONSENSUS-strength lens** — it works when it agrees with v3+jerry.
Its 72% on 7/21 was almost certainly on games where panel AGREED with
other lenses, boosting confluence.

## How to apply

- If a lens is a contrarian voice (only 1 layer disagreeing with the other 3),
  fade its signal, don't feature it.
- Prefer picks where 3+ lenses AGREE. When only 2 agree, drop conviction cap.
- The "contrarian value" narrative popular in betting content is not
  supported by our data at 60d n=13-33.

## Related

- [[project_audit_721_full]] — 7/21 audit that surfaced the MIN ML +114 flag
- [[project_model_reweight_721]] — 60d reweight audit that set panel weights
- [[feedback_let_engine_speak]] — trust net engine count over narrative
- [[feedback_confidence_in_first_pass]] — honest confidence bounds
