---
name: sharp-money-fade-808
description: "OddsCrowd sharp-money divergence is a FADE signal in current sample. Phase 2A shipped 8/8 evening — cap-to-LEAN active on TOTAL sharp_20+ w/ recency kill switch. HIGH-PRIORITY tracked item."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-09T12:13:06.068Z
---

**8/8 finding (n=132 games, 7 days of snapshots)**: sharp money divergence (from OddsCrowd) has been LOSING recently:
- Sharp 30+pp TOTAL: **17% hit** (n=6)
- Sharp 20+pp TOTAL: 42% (n=12) — **CAP ACTIVE (Phase 2A)**
- Sharp 20+pp ML: 20% (n=5) — n too thin for cap
- Sharp 10+pp ML: **41% hit** (n=22)
- Sharp+Jerry disagree on ML: Jerry 67% vs Sharp 33% (n=15)
- Aligned (small div): ML 57.6%, TOTAL 48.4%
- Public 10+pp: TOTAL 62.5% (n=8)
- **8/8 evening**: BOS lost 7-3 to A's with sharp on HOME +14pp — adds one more fade data point

**Blind-fade P&L (this-week snapshot, 8/8)**:
- ML fade div≥15: 6-2, +43.2% ROI (n=8)
- ML fade div≥20: 3-1, +43.2% ROI (n=4)
- TOTAL fade div≥20: 5-3, +19.3% ROI (n=8)
- TOTAL fade div≥30: 2-1, +27.3% ROI (n=3)

**Why**: Directly inverts Jerry's prior prompt (which treated sharp money 89% as bullish). Root cause hypothesized: OddsCrowd may track retail/steam moves rather than true institutional money — steam from weather/injury resolves before start and shows up as "sharp divergence."

**PHASED INTEGRATION (approved by user 8/8, Option A shipped evening)**:
1. **Phase 1** — LOG ONLY (early 8/8): `sharp_divergence_tracker.py` writes nightly, `sharp_fade_flag.py` returns flags, `tier_discipline_gate` prints `📊 sharp-fade` warnings.
2. **Phase 2A (SHIPPED 8/8 evening)** — CAP-TO-LEAN when pick aligns with sharp_20+ or sharp_30+ bucket where n≥12 AND lifetime hit%<45 AND recent-7d hit%<55. Currently TOTAL sharp_20+ CAP ACTIVE; others n-thin or above threshold.
3. **Phase 2B (when ML sharp_20+ reaches n≥12)** — extend cap to ML.
4. **Phase 3 (n≥500)** — full auto-fade: 30+pp = flip or SKIP; folded into `signal_confluence_v2_net`.

**Recency kill switch**: any CAP ACTIVE bucket auto-deactivates if last-7d sharp win% ≥ 55% (edge has reversed). Tracker updates `cap_active` flag nightly.

**HIGH-LEVEL TRACKED ITEM (user elevated 8/8)**: Monitor weekly via `sharp_money_weekly_digest.py` (runs Sundays via GHA cron, output stored `jerry_cache['sharp_money_weekly_digest_YYYY-WW']`). Report includes week-over-week bucket delta, blind-fade P&L, recency kill candidates. Sunday session task: review digest, decide hold / strengthen / relax cap.

**Kill/relax triggers to watch**:
- Recency kill switch tripped 2 weeks in a row → disable cap in that bucket
- Weekly ROI negative 2 weeks in a row for a threshold → downgrade to log-only
- New bucket crosses n≥12 with <45% hit → add to CAP ACTIVE list

**How to apply in-session**:
- When user asks about tonight's card, run sharp fade check on each Jerry pick
- When answering "what should I reconsider", surface sharp_20+ aligned picks first
- Weekly digest = high-signal for whether the fade thesis is holding
- Jerry template v5+ already updated to not cite sharp money as bullish

**File map**:
- `mlb_pipeline/sharp_divergence_tracker.py` — bucket stats (nightly)
- `mlb_pipeline/sharp_fade_rules.py` — 8 pattern rules + rule engine
- `mlb_pipeline/sharp_fade_rules_stats.py` — per-rule stats (nightly)
- `mlb_pipeline/sharp_fade_audit.py` — audit trail writer + self-calibration
- `mlb_pipeline/sharp_fade_flag.py` — helper reading bucket cache
- `mlb_pipeline/sharp_pattern_dashboard.py` — daily operator dashboard
- `mlb_pipeline/sharp_money_weekly_digest.py` — Sunday weekly digest
- `mlb_pipeline/tier_discipline_gate.py` — applies caps via `_apply_sharp_fade_cap`
- `supabase/migrations/20260809_sharp_fade_audit_trail.sql` — audit trail table
- `jerry_cache['sharp_divergence_stats']` — bucket live stats
- `jerry_cache['sharp_fade_rules_stats']` (GLOBAL_RULES) — per-rule live stats
- `jerry_cache['sharp_money_weekly_digest_YYYY-WW']` — weekly reports
- `sharp_fade_audit_trail` table — per-game per-sport audit trail

**2026-08-09 update — Phase 2B RULE-BASED CAP shipped**:
Beyond bucket-level caps, 8 pattern rules now cap picks that align with historical fade patterns:

| Rule | Sharp hit | n | Mode | Source of fade |
|---|---|---|---|---|
| SHARP_ON_ROAD_TEAM | 26% | 19 | ACTIVE | Sharp on AWAY teams loses 74% |
| MODELS_OPPOSE_SHARP_ML | 30% | 10 | ACTIVE | Both models disagree with sharp |
| SHARP_ON_AWAY_FAV | 30% | 10 | ACTIVE | Sharp on road favorites (subset R6) |
| SHARP_LIGHT_JUICE | 32% | 22 | ACTIVE | Sharp on -140 to +150 price band |
| SHARP_OPPOSES_CONFLUENCE | 33% | 30 | ACTIVE | Cohort net direction ≠ sharp |
| MODELS_OPPOSE_SHARP_TOTAL | 50% | 14 | LOG | Not below threshold yet |
| SHARP_OVER_HIGH_TOTAL | 38% | 8 | LOG | Small n |
| LEAN_TIER_SHARP_PILE_IN | 44% | 54 | LOG | Right at threshold |
| NON_DIV_SHARP_FADE | 42% | 72 | LOG | Amplifier only |

Policy: 1 ACTIVE rule → CAP_TO_LEAN. 2+ ACTIVE rules → CAP_TO_READ (49). Self-calibration adjusts thresholds nightly against actual market baseline.

**Self-calibration**: `sharp_fade_audit.py --calibrate` runs nightly. Reads audit trail, computes baseline (aggregate pick hit rate), sets:
- `life_ceiling = baseline - 5pp` (fade cap must beat baseline)
- `kill_switch = baseline + 5pp` (rule auto-DISABLED if sharp beats this)
- `n_min` remains per-rule floor (statistical significance)

Every game / every sport gets audit_trail row → cross-sport correlation possible as UFC/NFL/NCAAF pipelines mature.

Related: [[project_v4_over_drift]] (analogous "trust the signal or fade it" analysis), [[feedback_sharp_money_discipline_802]], [[feedback_verify_ml_direction]].
